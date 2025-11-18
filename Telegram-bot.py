import pandas as pd
import numpy as np
import os
import logging
import asyncio
from io import BytesIO
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class StudentProcessorBot:
    def __init__(self):
        self.user_sessions = {}
    
    def convert_grade(self, grade):
        """Конвертирует оценку из 10-бальной в 5-бальную систему"""
        if pd.isna(grade) or grade in ['-', '', 'н', 'Н']:
            return 'н'

        try:
            grade_num = float(str(grade).replace(',', '.'))
            if grade_num >= 9:
                return 5
            elif grade_num >= 7:
                return 4
            elif grade_num >= 5:
                return 3
            elif grade_num >= 3:
                return 2
            else:
                return 1
        except (ValueError, TypeError):
            return 'н'

    def read_students_list(self, file_content):
        """Читает список студентов из файла"""
        try:
            # Пробуем разные варианты чтения файла
            try:
                df_students = pd.read_excel(BytesIO(file_content), header=None, skiprows=2)
            except:
                df_students = pd.read_excel(BytesIO(file_content), header=None)
            
            student_dict = {}

            for index, row in df_students.iterrows():
                # Пробуем разные комбинации столбцов
                for i in range(min(5, len(row))):
                    if pd.notna(row[i]):
                        name_str = str(row[i]).strip()
                        # Ищем строку, похожую на ФИО (содержит буквы и пробелы)
                        if (any(c.isalpha() for c in name_str) and 
                            ' ' in name_str and 
                            not name_str.isdigit() and
                            len(name_str) > 3):
                            
                            # Следующий столбец может быть группой
                            if i + 1 < len(row) and pd.notna(row[i + 1]):
                                group = str(row[i + 1]).strip()
                                if any(c.isdigit() for c in group):  # Группа обычно содержит цифры
                                    student_dict[name_str] = group
                                    break

            return student_dict

        except Exception as e:
            raise Exception(f"Ошибка при чтении списка студентов: {e}")

    def get_available_groups(self, student_dict):
        """Получает список доступных групп"""
        groups = set(student_dict.values())
        return sorted(list(groups))

    def get_available_tests(self, df_results):
        """Получает список доступных тестов"""
        test_columns = []
        
        for col in df_results.columns:
            col_str = str(col).lower()
            if any(keyword in col_str for keyword in ['тест', 'test', 'лекц', 'лаб', 'итог']):
                test_columns.append(col)

        lecture_tests = []
        lab_tests = []
        final_tests = []

        for test_col in test_columns:
            test_name = str(test_col)
            test_lower = test_name.lower()

            if 'итоговый' in test_lower or 'итог' in test_lower:
                final_tests.append((test_col, test_name))
            elif 'лекц' in test_lower:
                lecture_tests.append((test_col, test_name))
            else:
                lab_tests.append((test_col, test_name))

        return lecture_tests, lab_tests, final_tests

    def find_student_in_results(self, student_name, df_results):
        """Поиск студента в результатах"""
        student_name_clean = ' '.join(str(student_name).lower().split())
        
        for idx, row in df_results.iterrows():
            # Ищем в каждом столбце, который может содержать ФИО
            for col in df_results.columns:
                if pd.notna(row[col]):
                    cell_value = str(row[col]).strip().lower()
                    if student_name_clean in cell_value or cell_value in student_name_clean:
                        return idx, row
                        
                    # Проверяем части имени
                    name_parts = student_name_clean.split()
                    if len(name_parts) > 1:
                        if all(any(part in cell_value_part for cell_value_part in cell_value.split()) 
                               for part in name_parts[:2]):  # Проверяем только фамилию и имя
                            return idx, row

        return None, None

    async def process_data(self, user_id, selected_groups, export_lectures, export_labs, export_finals):
        """Обрабатывает данные и возвращает файлы"""
        session = self.user_sessions.get(user_id)
        if not session or 'df_results' not in session or 'student_dict' not in session:
            raise Exception("Данные не загружены")

        df_results = session['df_results']
        student_dict = session['student_dict']

        # Получаем доступные тесты
        available_lecture_tests, available_lab_tests, available_final_tests = self.get_available_tests(df_results)

        # Фильтруем студентов по выбранным группам
        filtered_students = {name: group for name, group in student_dict.items() if group in selected_groups}

        # Подготавливаем данные
        lecture_data = []
        lab_data = []
        final_data = []

        found_students = 0
        not_found_students = []

        for student_name, group in filtered_students.items():
            base_data = {'ФИО': student_name, 'Группа': group}
            idx, result_row = self.find_student_in_results(student_name, df_results)

            if result_row is not None:
                found_students += 1

                if export_lectures and available_lecture_tests:
                    lecture_row = base_data.copy()
                    for test_col, test_name in available_lecture_tests:
                        grade = result_row[test_col] if test_col in result_row else None
                        lecture_row[test_name] = self.convert_grade(grade)
                    lecture_data.append(lecture_row)

                if export_labs and available_lab_tests:
                    lab_row = base_data.copy()
                    for test_col, test_name in available_lab_tests:
                        grade = result_row[test_col] if test_col in result_row else None
                        lab_row[test_name] = self.convert_grade(grade)
                    lab_data.append(lab_row)

                if export_finals and available_final_tests:
                    final_row = base_data.copy()
                    for test_col, test_name in available_final_tests:
                        grade = result_row[test_col] if test_col in result_row else None
                        final_row[test_name] = self.convert_grade(grade)
                    final_data.append(final_row)

            else:
                not_found_students.append(student_name)

                # Заполняем 'н' для ненайденных студентов
                if export_lectures and available_lecture_tests:
                    lecture_row = base_data.copy()
                    for test_col, test_name in available_lecture_tests:
                        lecture_row[test_name] = 'н'
                    lecture_data.append(lecture_row)

                if export_labs and available_lab_tests:
                    lab_row = base_data.copy()
                    for test_col, test_name in available_lab_tests:
                        lab_row[test_name] = 'н'
                    lab_data.append(lab_row)

                if export_finals and available_final_tests:
                    final_row = base_data.copy()
                    for test_col, test_name in available_final_tests:
                        final_row[test_name] = 'н'
                    final_data.append(final_row)

        # Создаем файлы
        files = []

        if export_lectures and available_lecture_tests and lecture_data:
            df_lecture = pd.DataFrame(lecture_data)
            output = BytesIO()
            df_lecture.to_excel(output, index=False, engine='openpyxl')
            output.seek(0)
            files.append(('Лекции_результаты.xlsx', output))

        if export_labs and available_lab_tests and lab_data:
            df_lab = pd.DataFrame(lab_data)
            output = BytesIO()
            df_lab.to_excel(output, index=False, engine='openpyxl')
            output.seek(0)
            files.append(('Лабораторные_результаты.xlsx', output))

        if export_finals and available_final_tests and final_data:
            df_final = pd.DataFrame(final_data)
            output = BytesIO()
            df_final.to_excel(output, index=False, engine='openpyxl')
            output.seek(0)
            files.append(('Итоговые_результаты.xlsx', output))

        return files, found_students, len(not_found_students)

# Создаем экземпляр бота
bot_processor = StudentProcessorBot()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in bot_processor.user_sessions:
        bot_processor.user_sessions[user_id] = {
            'step': None,
            'df_results': None,
            'student_dict': None,
            'available_groups': [],
            'selected_groups': [],
            'export_lectures': True,
            'export_labs': True,
            'export_finals': True
        }
    
    keyboard = [
        [InlineKeyboardButton("📊 Загрузить результаты", callback_data="load_results")],
        [InlineKeyboardButton("👥 Загрузить студентов", callback_data="load_students")],
        [InlineKeyboardButton("⚙️ Настройки", callback_data="configure")],
        [InlineKeyboardButton("🔄 Обработать", callback_data="process")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🤖 Бот для обработки результатов студентов\n\n"
        "Порядок действий:\n"
        "1. 📊 Загрузите таблицу с результатами\n"
        "2. 👥 Загрузите список студентов\n" 
        "3. ⚙️ Настройте параметры\n"
        "4. 🔄 Запустите обработку\n\n"
        "Выберите действие:",
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    if data == "load_results":
        bot_processor.user_sessions[user_id]['step'] = 'waiting_results'
        await query.edit_message_text("📊 Отправьте Excel файл с результатами тестов")
    
    elif data == "load_students":
        bot_processor.user_sessions[user_id]['step'] = 'waiting_students'
        await query.edit_message_text("👥 Отправьте Excel файл со списком студентов")
    
    elif data == "configure":
        session = bot_processor.user_sessions.get(user_id)
        if not session or session['df_results'] is None or session['student_dict'] is None:
            await query.edit_message_text("❌ Сначала загрузите оба файла")
            return
        
        available_groups = bot_processor.get_available_groups(session['student_dict'])
        session['available_groups'] = available_groups
        
        keyboard = []
        for group in available_groups:
            is_selected = group in session.get('selected_groups', [])
            emoji = "✅" if is_selected else "❌"
            keyboard.append([InlineKeyboardButton(f"{emoji} {group}", callback_data=f"toggle_group_{group}")])
        
        keyboard.append([InlineKeyboardButton("📤 Типы экспорта", callback_data="export_types")])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        selected_count = len(session.get('selected_groups', []))
        await query.edit_message_text(
            f"⚙️ Выберите группы ({selected_count}/{len(available_groups)} выбрано):",
            reply_markup=reply_markup
        )
    
    elif data == "process":
        session = bot_processor.user_sessions.get(user_id)
        if not session:
            await query.edit_message_text("❌ Начните с /start")
            return
        
        selected_groups = session.get('selected_groups', [])
        if not selected_groups:
            await query.edit_message_text("❌ Выберите группы в настройках")
            return
        
        await query.edit_message_text("🔄 Обрабатываю данные...")
        
        try:
            files, found_count, not_found_count = await bot_processor.process_data(
                user_id, 
                selected_groups, 
                session.get('export_lectures', True),
                session.get('export_labs', True), 
                session.get('export_finals', True)
            )
            
            if not files:
                await query.edit_message_text("❌ Не удалось создать файлы")
                return
            
            for filename, file_data in files:
                await context.bot.send_document(
                    chat_id=query.message.chat_id,
                    document=file_data,
                    filename=filename
                )
            
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"✅ Готово!\nНайдено: {found_count}\nНе найдено: {not_found_count}"
            )
            
        except Exception as e:
            await query.edit_message_text(f"❌ Ошибка: {str(e)}")
    
    elif data.startswith("toggle_group_"):
        group = data.replace("toggle_group_", "")
        session = bot_processor.user_sessions.get(user_id)
        
        if 'selected_groups' not in session:
            session['selected_groups'] = []
        
        if group in session['selected_groups']:
            session['selected_groups'].remove(group)
        else:
            session['selected_groups'].append(group)
        
        await button_handler(update, context)  # Обновляем сообщение
    
    elif data == "export_types":
        session = bot_processor.user_sessions.get(user_id)
        keyboard = [
            [InlineKeyboardButton(f"{'✅' if session.get('export_lectures', True) else '❌'} Лекции", callback_data="toggle_lectures")],
            [InlineKeyboardButton(f"{'✅' if session.get('export_labs', True) else '❌'} Лабораторные", callback_data="toggle_labs")],
            [InlineKeyboardButton(f"{'✅' if session.get('export_finals', True) else '❌'} Итоговые", callback_data="toggle_finals")],
            [InlineKeyboardButton("🔙 Назад", callback_data="configure")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("📤 Выберите типы тестов:", reply_markup=reply_markup)
    
    elif data in ["toggle_lectures", "toggle_labs", "toggle_finals"]:
        session = bot_processor.user_sessions.get(user_id)
        key = data.replace("toggle_", "")
        session[key] = not session.get(key, True)
        await button_handler(update, context)
    
    elif data == "back_to_main":
        keyboard = [
            [InlineKeyboardButton("📊 Загрузить результаты", callback_data="load_results")],
            [InlineKeyboardButton("👥 Загрузить студентов", callback_data="load_students")],
            [InlineKeyboardButton("⚙️ Настройки", callback_data="configure")],
            [InlineKeyboardButton("🔄 Обработать", callback_data="process")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Выберите действие:", reply_markup=reply_markup)

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = bot_processor.user_sessions.get(user_id)
    
    if not session or session['step'] is None:
        await update.message.reply_text("❌ Сначала выберите действие")
        return
    
    document = update.message.document
    file = await context.bot.get_file(document.file_id)
    file_content = await file.download_as_bytearray()
    
    try:
        if session['step'] == 'waiting_results':
            df = pd.read_excel(BytesIO(file_content))
            session['df_results'] = df
            session['step'] = None
            
            lecture_tests, lab_tests, final_tests = bot_processor.get_available_tests(df)
            await update.message.reply_text(
                f"✅ Результаты загружены!\n"
                f"Лекции: {len(lecture_tests)}\n"
                f"Лабы: {len(lab_tests)}\n" 
                f"Итоговые: {len(final_tests)}"
            )
        
        elif session['step'] == 'waiting_students':
            student_dict = bot_processor.read_students_list(file_content)
            session['student_dict'] = student_dict
            session['step'] = None
            
            groups = bot_processor.get_available_groups(student_dict)
            await update.message.reply_text(
                f"✅ Студенты загружены!\n"
                f"Студентов: {len(student_dict)}\n"
                f"Групп: {len(groups)}"
            )
    
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Ошибка: {context.error}")
    if update and update.effective_message:
        await update.effective_message.reply_text("❌ Ошибка. Попробуйте /start")

def main():
    TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    if not TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN не установлен")
        return
    
    application = Application.builder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_error_handler(error_handler)
    
    print("🤖 Бот запущен...")
    application.run_polling()

if __name__ == "__main__":
    main()
