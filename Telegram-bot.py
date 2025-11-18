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
        if pd.isna(grade) or grade == '-' or grade == '':
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
            df_students = pd.read_excel(BytesIO(file_content), header=None, skiprows=2)
            student_dict = {}

            for index, row in df_students.iterrows():
                if len(row) >= 3 and pd.notna(row[1]) and pd.notna(row[2]):
                    full_name = str(row[1]).strip()
                    group = str(row[2]).strip()

                    if full_name and not full_name.isdigit():
                        student_dict[full_name] = group

            return student_dict

        except Exception as e:
            raise Exception(f"Ошибка при чтении списка студентов: {e}")

    def get_available_groups(self, student_dict):
        """Получает список доступных групп"""
        groups = set(student_dict.values())
        return sorted(list(groups))

    def get_available_tests(self, df_results):
        """Получает список доступных тестов"""
        test_columns = [col for col in df_results.columns if 'тест' in str(col).lower()]

        lecture_tests = []
        lab_tests = []
        final_tests = []

        for test_col in test_columns:
            category, test_name = self.categorize_test(test_col)
            if category == 'lecture':
                lecture_tests.append((test_col, test_name))
            elif category == 'final':
                final_tests.append((test_col, test_name))
            else:
                lab_tests.append((test_col, test_name))

        lecture_tests.sort(key=lambda x: x[1])
        lab_tests.sort(key=lambda x: x[1])
        final_tests.sort(key=lambda x: x[1])

        return lecture_tests, lab_tests, final_tests

    def categorize_test(self, test_name):
        """Категоризирует тесты"""
        test_lower = str(test_name).lower()

        if 'итоговый' in test_lower or 'итог' in test_lower:
            return 'final', test_name
        elif 'лекц' in test_lower:
            return 'lecture', test_name
        else:
            return 'lab', test_name

    def find_student_in_results(self, student_name, df_results):
        """Упрощенный поиск студента по ФИО"""
        # Нормализуем имя студента для поиска
        student_name_norm = ' '.join(str(student_name).lower().split())
        
        for idx, result_row in df_results.iterrows():
            # Ищем столбцы с ФИО
            for col in df_results.columns:
                col_lower = str(col).lower()
                if any(keyword in col_lower for keyword in ['фио', 'фамилия', 'имя', 'студент']):
                    if pd.notna(result_row[col]):
                        result_name = str(result_row[col]).strip().lower()
                        # Простое сравнение по вхождению
                        if (student_name_norm in result_name or 
                            result_name in student_name_norm or
                            any(part in result_name for part in student_name_norm.split())):
                            return idx, result_row
        
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

        # Подготавливаем данные для каждого типа экспорта
        lecture_data_by_group = {}
        lab_data_by_group = {}
        final_data_by_group = {}

        # Инициализируем словари для каждой группы
        for group in selected_groups:
            lecture_data_by_group[group] = []
            lab_data_by_group[group] = []
            final_data_by_group[group] = []

        found_students = 0
        not_found_students = []

        # Обрабатываем каждого студента
        for student_name, group in filtered_students.items():
            # Базовые данные студента
            base_data = {'ФИО': student_name, 'Группа': group}

            # Ищем студента в результатах
            idx, result_row = self.find_student_in_results(student_name, df_results)

            if result_row is not None:
                found_students += 1

                # Лекции
                if export_lectures and available_lecture_tests:
                    lecture_row = base_data.copy()
                    for test_col, test_name in available_lecture_tests:
                        grade = result_row[test_col] if test_col in result_row else None
                        lecture_row[test_name] = self.convert_grade(grade)
                    lecture_data_by_group[group].append(lecture_row)

                # Лабораторные
                if export_labs and available_lab_tests:
                    lab_row = base_data.copy()
                    for test_col, test_name in available_lab_tests:
                        grade = result_row[test_col] if test_col in result_row else None
                        lab_row[test_name] = self.convert_grade(grade)
                    lab_data_by_group[group].append(lab_row)

                # Итоговые
                if export_finals and available_final_tests:
                    final_row = base_data.copy()
                    for test_col, test_name in available_final_tests:
                        grade = result_row[test_col] if test_col in result_row else None
                        final_row[test_name] = self.convert_grade(grade)
                    final_data_by_group[group].append(final_row)

            else:
                not_found_students.append(student_name)

                # Если студент не найден, заполняем 'н'
                if export_lectures and available_lecture_tests:
                    lecture_row = base_data.copy()
                    for test_col, test_name in available_lecture_tests:
                        lecture_row[test_name] = 'н'
                    lecture_data_by_group[group].append(lecture_row)

                if export_labs and available_lab_tests:
                    lab_row = base_data.copy()
                    for test_col, test_name in available_lab_tests:
                        lab_row[test_name] = 'н'
                    lab_data_by_group[group].append(lab_row)

                if export_finals and available_final_tests:
                    final_row = base_data.copy()
                    for test_col, test_name in available_final_tests:
                        final_row[test_name] = 'н'
                    final_data_by_group[group].append(final_row)

        # Создаем файлы в памяти
        files = []

        # Лекции
        if export_lectures and available_lecture_tests:
            lecture_dfs = {}
            for group, data in lecture_data_by_group.items():
                if data:
                    columns = ['ФИО', 'Группа'] + [test[1] for test in available_lecture_tests]
                    lecture_dfs[group] = pd.DataFrame(data, columns=columns)

            if lecture_dfs:
                output = BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    for group_name, group_data in lecture_dfs.items():
                        sheet_name = str(group_name)[:31]
                        group_data.to_excel(writer, sheet_name=sheet_name, index=False)
                output.seek(0)
                files.append(('Лекции_результаты.xlsx', output))

        # Лабораторные
        if export_labs and available_lab_tests:
            lab_dfs = {}
            for group, data in lab_data_by_group.items():
                if data:
                    columns = ['ФИО', 'Группа'] + [test[1] for test in available_lab_tests]
                    lab_dfs[group] = pd.DataFrame(data, columns=columns)

            if lab_dfs:
                output = BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    for group_name, group_data in lab_dfs.items():
                        sheet_name = str(group_name)[:31]
                        group_data.to_excel(writer, sheet_name=sheet_name, index=False)
                output.seek(0)
                files.append(('Лабораторные_результаты.xlsx', output))

        # Итоговые
        if export_finals and available_final_tests:
            final_dfs = {}
            for group, data in final_data_by_group.items():
                if data:
                    columns = ['ФИО', 'Группа'] + [test[1] for test in available_final_tests]
                    final_dfs[group] = pd.DataFrame(data, columns=columns)

            if final_dfs:
                output = BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    for group_name, group_data in final_dfs.items():
                        sheet_name = str(group_name)[:31]
                        group_data.to_excel(writer, sheet_name=sheet_name, index=False)
                output.seek(0)
                files.append(('Итоговые_результаты.xlsx', output))

        return files, found_students, len(not_found_students)

# Создаем экземпляр бота
bot_processor = StudentProcessorBot()

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # Инициализируем сессию пользователя
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
        [InlineKeyboardButton("📊 Загрузить таблицу с результатами", callback_data="load_results")],
        [InlineKeyboardButton("👥 Загрузить список студентов", callback_data="load_students")],
        [InlineKeyboardButton("⚙️ Настроить обработку", callback_data="configure")],
        [InlineKeyboardButton("🔄 Обработать данные", callback_data="process")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🤖 Бот для обработки результатов студентов\n\n"
        "Для начала работы:\n"
        "1. Загрузите таблицу с результатами тестов\n"
        "2. Загрузите список студентов\n"
        "3. Настройте параметры обработки\n"
        "4. Запустите обработку\n\n"
        "Выберите действие:",
        reply_markup=reply_markup
    )

# Обработчик кнопок
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    if data == "load_results":
        bot_processor.user_sessions[user_id]['step'] = 'waiting_results'
        await query.edit_message_text(
            "📊 Отправьте файл Excel с результатами тестов\n\n"
            "Файл должен содержать столбцы с названиями тестов."
        )
    
    elif data == "load_students":
        bot_processor.user_sessions[user_id]['step'] = 'waiting_students'
        await query.edit_message_text(
            "👥 Отправьте файл Excel со списком студентов\n\n"
            "Формат файла:\n"
            "- Пропустите 2 первые строки\n"
            "- ФИО студентов во втором столбце\n"
            "- Группы в третьем столбце"
        )
    
    elif data == "configure":
        session = bot_processor.user_sessions.get(user_id)
        if not session or session['df_results'] is None or session['student_dict'] is None:
            await query.edit_message_text(
                "❌ Сначала загрузите оба файла: таблицу с результатами и список студентов."
            )
            return
        
        # Получаем доступные группы
        available_groups = bot_processor.get_available_groups(session['student_dict'])
        session['available_groups'] = available_groups
        
        # Создаем клавиатуру для выбора групп
        keyboard = []
        for group in available_groups:
            is_selected = group in session.get('selected_groups', [])
            emoji = "✅" if is_selected else "❌"
            keyboard.append([InlineKeyboardButton(f"{emoji} {group}", callback_data=f"toggle_group_{group}")])
        
        keyboard.append([InlineKeyboardButton("✅ Выбрать все", callback_data="select_all_groups")])
        keyboard.append([InlineKeyboardButton("❌ Снять все", callback_data="deselect_all_groups")])
        keyboard.append([InlineKeyboardButton("📤 Типы экспорта", callback_data="export_types")])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        selected_count = len(session.get('selected_groups', []))
        await query.edit_message_text(
            f"⚙️ Настройка обработки\n\n"
            f"Выберите группы для обработки:\n"
            f"Выбрано: {selected_count}/{len(available_groups)}\n\n"
            f"Список групп:",
            reply_markup=reply_markup
        )
    
    elif data == "process":
        session = bot_processor.user_sessions.get(user_id)
        if not session:
            await query.edit_message_text("❌ Сессия не найдена. Начните с /start")
            return
        
        selected_groups = session.get('selected_groups', [])
        export_lectures = session.get('export_lectures', True)
        export_labs = session.get('export_labs', True)
        export_finals = session.get('export_finals', True)
        
        if not selected_groups:
            await query.edit_message_text("❌ Выберите хотя бы одну группу в настройках.")
            return
        
        if not (export_lectures or export_labs or export_finals):
            await query.edit_message_text("❌ Выберите хотя бы один тип экспорта в настройках.")
            return
        
        await query.edit_message_text("🔄 Начинаю обработку данных... Это может занять несколько минут.")
        
        try:
            files, found_count, not_found_count = await bot_processor.process_data(
                user_id, selected_groups, export_lectures, export_labs, export_finals
            )
            
            if not files:
                await query.edit_message_text("❌ Не удалось создать файлы. Проверьте данные.")
                return
            
            # Отправляем файлы
            for filename, file_data in files:
                await context.bot.send_document(
                    chat_id=query.message.chat_id,
                    document=file_data,
                    filename=filename
                )
            
            # Статистика
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"✅ Обработка завершена!\n\n"
                     f"📊 Статистика:\n"
                     f"• Найдено студентов: {found_count}\n"
                     f"• Не найдено: {not_found_count}\n"
                     f"• Создано файлов: {len(files)}"
            )
            
        except Exception as e:
            await query.edit_message_text(f"❌ Ошибка при обработке: {str(e)}")
    
    elif data.startswith("toggle_group_"):
        group = data.replace("toggle_group_", "")
        session = bot_processor.user_sessions.get(user_id)
        
        if 'selected_groups' not in session:
            session['selected_groups'] = []
        
        if group in session['selected_groups']:
            session['selected_groups'].remove(group)
        else:
            session['selected_groups'].append(group)
        
        # Обновляем сообщение
        keyboard = []
        for grp in session['available_groups']:
            is_selected = grp in session['selected_groups']
            emoji = "✅" if is_selected else "❌"
            keyboard.append([InlineKeyboardButton(f"{emoji} {grp}", callback_data=f"toggle_group_{grp}")])
        
        keyboard.append([InlineKeyboardButton("✅ Выбрать все", callback_data="select_all_groups")])
        keyboard.append([InlineKeyboardButton("❌ Снять все", callback_data="deselect_all_groups")])
        keyboard.append([InlineKeyboardButton("📤 Типы экспорта", callback_data="export_types")])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        selected_count = len(session['selected_groups'])
        await query.edit_message_text(
            f"⚙️ Настройка обработки\n\n"
            f"Выберите группы для обработки:\n"
            f"Выбрано: {selected_count}/{len(session['available_groups'])}\n\n"
            f"Список групп:",
            reply_markup=reply_markup
        )
    
    elif data == "select_all_groups":
        session = bot_processor.user_sessions.get(user_id)
        session['selected_groups'] = session['available_groups'].copy()
        
        keyboard = []
        for group in session['available_groups']:
            keyboard.append([InlineKeyboardButton(f"✅ {group}", callback_data=f"toggle_group_{group}")])
        
        keyboard.append([InlineKeyboardButton("✅ Выбрать все", callback_data="select_all_groups")])
        keyboard.append([InlineKeyboardButton("❌ Снять все", callback_data="deselect_all_groups")])
        keyboard.append([InlineKeyboardButton("📤 Типы экспорта", callback_data="export_types")])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"⚙️ Настройка обработки\n\n"
            f"Выберите группы для обработки:\n"
            f"Выбрано: {len(session['selected_groups'])}/{len(session['available_groups'])}\n\n"
            f"Список групп:",
            reply_markup=reply_markup
        )
    
    elif data == "deselect_all_groups":
        session = bot_processor.user_sessions.get(user_id)
        session['selected_groups'] = []
        
        keyboard = []
        for group in session['available_groups']:
            keyboard.append([InlineKeyboardButton(f"❌ {group}", callback_data=f"toggle_group_{group}")])
        
        keyboard.append([InlineKeyboardButton("✅ Выбрать все", callback_data="select_all_groups")])
        keyboard.append([InlineKeyboardButton("❌ Снять все", callback_data="deselect_all_groups")])
        keyboard.append([InlineKeyboardButton("📤 Типы экспорта", callback_data="export_types")])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"⚙️ Настройка обработки\n\n"
            f"Выберите группы для обработки:\n"
            f"Выбрано: 0/{len(session['available_groups'])}\n\n"
            f"Список групп:",
            reply_markup=reply_markup
        )
    
    elif data == "export_types":
        session = bot_processor.user_sessions.get(user_id)
        export_lectures = session.get('export_lectures', True)
        export_labs = session.get('export_labs', True)
        export_finals = session.get('export_finals', True)
        
        keyboard = [
            [InlineKeyboardButton(f"{'✅' if export_lectures else '❌'} Лекции", callback_data="toggle_lectures")],
            [InlineKeyboardButton(f"{'✅' if export_labs else '❌'} Лабораторные", callback_data="toggle_labs")],
            [InlineKeyboardButton(f"{'✅' if export_finals else '❌'} Итоговые", callback_data="toggle_finals")],
            [InlineKeyboardButton("🔙 Назад к группам", callback_data="configure")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "📤 Выберите типы тестов для экспорта:",
            reply_markup=reply_markup
        )
    
    elif data == "toggle_lectures":
        session = bot_processor.user_sessions.get(user_id)
        session['export_lectures'] = not session.get('export_lectures', True)
        await button_handler(update, context)
    
    elif data == "toggle_labs":
        session = bot_processor.user_sessions.get(user_id)
        session['export_labs'] = not session.get('export_labs', True)
        await button_handler(update, context)
    
    elif data == "toggle_finals":
        session = bot_processor.user_sessions.get(user_id)
        session['export_finals'] = not session.get('export_finals', True)
        await button_handler(update, context)
    
    elif data == "back_to_main":
        keyboard = [
            [InlineKeyboardButton("📊 Загрузить таблицу с результатами", callback_data="load_results")],
            [InlineKeyboardButton("👥 Загрузить список студентов", callback_data="load_students")],
            [InlineKeyboardButton("⚙️ Настроить обработку", callback_data="configure")],
            [InlineKeyboardButton("🔄 Обработать данные", callback_data="process")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "🤖 Бот для обработки результатов студентов\n\n"
            "Выберите действие:",
            reply_markup=reply_markup
        )

# Обработчик документов
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = bot_processor.user_sessions.get(user_id)
    
    if not session or session['step'] is None:
        await update.message.reply_text("❌ Сначала выберите действие через меню.")
        return
    
    document = update.message.document
    file = await context.bot.get_file(document.file_id)
    file_content = await file.download_as_bytearray()
    
    try:
        if session['step'] == 'waiting_results':
            # Загружаем таблицу с результатами
            if document.file_name.endswith('.xls'):
                df = pd.read_excel(BytesIO(file_content), engine='xlrd')
            else:
                df = pd.read_excel(BytesIO(file_content), engine='openpyxl')
            
            session['df_results'] = df
            session['step'] = None
            
            # Получаем информацию о тестах
            lecture_tests, lab_tests, final_tests = bot_processor.get_available_tests(df)
            
            await update.message.reply_text(
                f"✅ Таблица с результатами загружена!\n\n"
                f"📊 Найдено тестов:\n"
                f"• Лекции: {len(lecture_tests)}\n"
                f"• Лабораторные: {len(lab_tests)}\n"
                f"• Итоговые: {len(final_tests)}"
            )
        
        elif session['step'] == 'waiting_students':
            # Загружаем список студентов
            student_dict = bot_processor.read_students_list(file_content)
            session['student_dict'] = student_dict
            session['step'] = None
            
            available_groups = bot_processor.get_available_groups(student_dict)
            
            await update.message.reply_text(
                f"✅ Список студентов загружен!\n\n"
                f"👥 Найдено:\n"
                f"• Студентов: {len(student_dict)}\n"
                f"• Групп: {len(available_groups)}"
            )
    
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка при загрузке файла: {str(e)}")

# Обработчик ошибок
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Ошибка: {context.error}", exc_info=context.error)
    
    if update and update.effective_message:
        await update.effective_message.reply_text(
            "❌ Произошла ошибка. Попробуйте еще раз или начните заново с /start"
        )

def main():
    # Получаем токен бота из переменных окружения
    TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    
    if not TOKEN:
        print("❌ Ошибка: TELEGRAM_BOT_TOKEN не установлен")
        return
    
    # Создаем приложение
    application = Application.builder().token(TOKEN).build()
    
    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_error_handler(error_handler)
    
    # Запускаем бота
    print("🤖 Бот запущен...")
    application.run_polling()

if __name__ == "__main__":
    main()
