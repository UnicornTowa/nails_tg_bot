import telebot
import psycopg2
from datetime import datetime, timedelta
from os import getenv

# Токен вашего бота
TOKEN = "7640122993:AAG1_UGs00uPKZHAHT84B0R0dulElqU8D2Q"
bot = telebot.TeleBot(TOKEN)

# Подключение к базе данных (замените на свои параметры)
DATABASE_URL = getenv("DATABASE_URL")

weekdays_mapping = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")

admin_ids = ['431404344', '422368785']


def get_db_connection():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
    CREATE OR REPLACE FUNCTION check_time(start_time TIMESTAMP, slot_count INT)
RETURNS BOOLEAN AS $$
DECLARE
    free_slots_count INT;
BEGIN
    SELECT COUNT(*)
    INTO free_slots_count
    FROM slots
    WHERE is_booked = FALSE
      AND datetime >= start_time
      AND datetime < start_time + INTERVAL '30 minutes' * slot_count;

    RETURN free_slots_count = slot_count;
END;
$$ LANGUAGE plpgsql;""")

    cur.execute("""CREATE OR REPLACE FUNCTION check_gap(start_time TIMESTAMP, slot_count int)
RETURNS BOOLEAN AS $$
DECLARE
    last_booked timestamp default null;
BEGIN
    select datetime from slots where is_booked=true and date(datetime) = date(start_time)
                               order by datetime desc limit 1 into last_booked;

    RETURN last_booked is null or
           start_time < last_booked and last_booked - (start_time + interval '30 minutes' * slot_count) <= interval '90 minutes' or
           start_time > last_booked and start_time - last_booked <= interval '90 minutes';
END;
$$ LANGUAGE plpgsql;""")

    cur.execute("set timezone to '+3'")

    conn.commit()
    cur.close()
    conn.close()


def main_menu(chat_id):
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    button_book = telebot.types.KeyboardButton("Записаться")
    button_appointments = telebot.types.KeyboardButton("Мои записи")
    markup.add(button_book, button_appointments)
    bot.send_message(chat_id, "Добро пожаловать! Выберите нужное действие:", reply_markup=markup)


@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    username = message.from_user.username
    full_name = f"{message.from_user.first_name} {message.from_user.last_name or ''}"

    # Регистрируем пользователя в базе данных
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO users (id, username, full_name)
        VALUES (%s, %s, %s)
        ON CONFLICT (id) DO NOTHING
    """, (user_id, username, full_name.strip()))
    conn.commit()

    cursor.close()
    conn.close()
    main_menu(message.chat.id)


@bot.message_handler(func=lambda message: message.text == 'Записаться' or message.text == '/start_booking')
def select_service(message):
    # Формируем кнопки для выбора времени
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(
        telebot.types.InlineKeyboardButton(text='Маникюр', callback_data='manicure'),
        telebot.types.InlineKeyboardButton(text='Наращивание', callback_data='extension'),
        telebot.types.InlineKeyboardButton(text='Коррекция наращивания', callback_data='ext_cor'),
        telebot.types.InlineKeyboardButton(text='Педикюр', callback_data='pedicure'), row_width=2
    )

    bot.send_message(message.chat.id, "Выберите услугу:", reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data == "reset")
def reset(call):
    main_menu(call.message.chat.id)
    bot.answer_callback_query(call.id, 'Вы вернулись в главное меню')


@bot.callback_query_handler(func=lambda call: call.data == "working_days")
def working_days(call):
    conn = get_db_connection()
    cursor = conn.cursor()

    today = datetime.today()
    days = [(today + timedelta(days=i),) for i in range(14)]
    cursor.executemany("insert into days values (%s) on conflict do nothing;", days)
    conn.commit()

    cursor.execute("select * from days where day >= %s order by day", (today.date(),))
    data = cursor.fetchall()

    markup = telebot.types.InlineKeyboardMarkup()
    for date, is_working in data:
        markup.add(telebot.types.InlineKeyboardButton(text=f"{date.strftime('%d.%m.%Y')}, "
                                                           f"({weekdays_mapping[date.weekday()]}), "
                                                           f"{'Раб' if is_working else 'Вых'}",
                                                      callback_data=f"change_day_{date}"))
    markup.add(telebot.types.InlineKeyboardButton(text='Обновить дни', callback_data='working_days'))
    markup.add(telebot.types.InlineKeyboardButton(text='Выйти в главное меню', callback_data='reset'))
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "Кликните на день чтобы изменить его статус", reply_markup=markup)

    cursor.close()
    conn.close()


@bot.callback_query_handler(func=lambda call: call.data.startswith('change_day_'))
def update_day(call):
    conn = get_db_connection()
    cursor = conn.cursor()
    date = datetime.strptime(call.data.split('_')[2], '%Y-%m-%d').date()

    cursor.execute("select is_working from days where day = %s", (date,))
    is_true = cursor.fetchall()[0][0]
    cursor.execute("update days set is_working = %s where day = %s", (not is_true, date,))

    if is_true:  # working -> non-working
        delete_working_slots(date)
    else:  # non_working -> working
        add_working_slots(date)

    conn.commit()
    cursor.close()
    conn.close()

    bot.answer_callback_query(call.id, 'Успешно')


@bot.message_handler(commands=['root'])
def root_menu(message):
    is_root = str(message.from_user.id) in admin_ids

    if not is_root:
        bot.send_message(message.chat.id, "Недостаточно прав.")
        main_menu(message.chat.id)

    else:
        markup = telebot.types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            telebot.types.InlineKeyboardButton(text='Рабочие дни', callback_data='working_days'),
            telebot.types.InlineKeyboardButton(text='Просмотреть записи', callback_data='show_apps'),
            telebot.types.InlineKeyboardButton(text='Выйти в главное меню', callback_data='reset')
        )
        bot.send_message(message.chat.id, "Выберите нужное действие:", reply_markup=markup)


def add_working_slots(date, start_time='12:00', end_time='23:00', slot_duration=30):
    """
    Добавляет рабочие слоты на указанный день.

    :param date: День в формате 'YYYY-MM-DD'
    :param start_time: Начало рабочего времени, например, '09:00'
    :param end_time: Конец рабочего времени, например, '18:00'
    :param slot_duration: Длительность одного слота в минутах (по умолчанию 30)
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Преобразование времени
        start_datetime = datetime.strptime(f"{date} {start_time}", "%Y-%m-%d %H:%M")
        end_datetime = datetime.strptime(f"{date} {end_time}", "%Y-%m-%d %H:%M")
        current_time = start_datetime

        # Добавление слотов в базу данных
        while current_time < end_datetime:
            cursor.execute("""
                INSERT INTO slots
                VALUES (default, %s, default)
                ON CONFLICT (datetime) DO NOTHING;
            """, (current_time,))
            current_time += timedelta(minutes=slot_duration)

        conn.commit()
        print("Рабочие слоты успешно добавлены.")
    except Exception as e:
        print("Ошибка при добавлении слотов:", e)
    finally:
        cursor.close()
        conn.close()


def delete_working_slots(date):
    """
    Удаляет все рабочие слоты для указанного дня.

    :param date: День в формате 'YYYY-MM-DD'
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Удаление слотов
        cursor.execute("""
            DELETE FROM slots
            WHERE DATE(datetime) = %s AND is_booked = FALSE;
        """, (date,))
        conn.commit()
        print("Рабочие слоты успешно удалены.")
    except Exception as e:
        print("Ошибка при удалении слотов:", e)
    finally:
        cursor.close()
        conn.close()


@bot.callback_query_handler(func=lambda call: call.data == "manicure")
def manicure(call):
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        telebot.types.InlineKeyboardButton(text='Короткие ногти', callback_data='manic_short'),
        telebot.types.InlineKeyboardButton(text='Длинные ногти', callback_data='manic_long'),
        telebot.types.InlineKeyboardButton(text='Без покрытия', callback_data='sel_manic_sim'),
    )

    bot.send_message(call.message.chat.id, 'Выберите услугу (маникюр c покрытием):', reply_markup=markup)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "manic_short")
def manic_short(call):
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        telebot.types.InlineKeyboardButton(text='С укреплением', callback_data='manic_short_reinf'),
        telebot.types.InlineKeyboardButton(text='Без укрепления', callback_data='manic_short_noreinf')
    )

    bot.send_message(call.message.chat.id, 'Выберите услугу (маникюр c покрытием, короткие ногти):',
                     reply_markup=markup)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("manic_short_"))
def manic_short_reinf(call):
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        telebot.types.InlineKeyboardButton(text='C дизайном', callback_data='sel_' + call.data + '_des'),
        telebot.types.InlineKeyboardButton(text='Без дизайна', callback_data='sel_' + call.data + '_nodes'),
    )

    reinf = 'без укрепления' if call.data.split('_')[2] == 'noreinf' else 'с укреплением'
    bot.send_message(call.message.chat.id, f'Выберите услугу (маникюр c покрытием, короткие ногти, {reinf}):',
                     reply_markup=markup)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "manic_long")
def manic_long(call):
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        telebot.types.InlineKeyboardButton(text='С дизайном', callback_data='sel_manic_long_des'),
        telebot.types.InlineKeyboardButton(text='Без дизайна', callback_data='sel_manic_long_sim')
    )

    bot.send_message(call.message.chat.id, 'Выберите услугу (маникюр c покрытием, длинные ногти):',
                     reply_markup=markup)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "extension")
def extension(call):
    markup = telebot.types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        telebot.types.InlineKeyboardButton(text='Длинные ногти, с дизайном', callback_data='sel_ext_long_des'),
        telebot.types.InlineKeyboardButton(text='Длинные ногти, без дизайна', callback_data='sel_ext_long_sim'),
        telebot.types.InlineKeyboardButton(text='Короткие ногти, с дизайном', callback_data='sel_ext_short_des'),
        telebot.types.InlineKeyboardButton(text='Короткие ногти, без дизайна', callback_data='sel_ext_short_sim')
    )

    bot.send_message(call.message.chat.id, 'Прим. короткие (1-2 длины), длинные (3+) \n'
                                           'Выберите услугу (наращивание):',
                     reply_markup=markup)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "ext_cor")
def ext_cor(call):
    markup = telebot.types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        telebot.types.InlineKeyboardButton(text='Длинные ногти, с дизайном', callback_data='sel_ext_cor_long_des'),
        telebot.types.InlineKeyboardButton(text='Длинные ногти, без дизайна', callback_data='sel_ext_cor_long_sim'),
        telebot.types.InlineKeyboardButton(text='Короткие ногти, с дизайном', callback_data='sel_ext_cor_short_des'),
        telebot.types.InlineKeyboardButton(text='Короткие ногти, без дизайна', callback_data='sel_ext_cor_short_sim')
    )

    bot.send_message(call.message.chat.id, 'Прим. короткие (1-2 длины), длинные (3+) \n'
                                           'Выберите услугу (коррекция наращивания):',
                     reply_markup=markup)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "pedicure")
def pedicure(call):
    markup = telebot.types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        telebot.types.InlineKeyboardButton(text='Пятка и пальчики, с покрытием', callback_data='sel_ped_coat_heel'),
        telebot.types.InlineKeyboardButton(text='Пятка и пальчики, без покрытия', callback_data='sel_ped_heel'),
        telebot.types.InlineKeyboardButton(text='Только пальчики, с покрытием', callback_data='sel_ped_coat_finger'),
        telebot.types.InlineKeyboardButton(text='Только пальчики, без покрытия', callback_data='sel_ped_finger')
    )

    bot.send_message(call.message.chat.id, 'Выберите услугу (педикюр):',
                     reply_markup=markup)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith('sel_'))
def select_day(call):
    conn = get_db_connection()
    cur = conn.cursor()

    name = code_to_name[call.data]
    duration = code_to_duration[call.data]

    cur.execute("""select date(datetime) as slot_date,
       count(*) as free_slot_count
from slots
where datetime > now()
  and check_time(datetime, %s)
  and check_gap(datetime, %s)
group by date(datetime)
order by slot_date""", (duration, duration))

    dates = cur.fetchall()

    cur.close()
    conn.close()

    if len(dates) == 0:
        bot.answer_callback_query(call.id, 'Нет доступных дат')

    else:
        markup = telebot.types.InlineKeyboardMarkup(row_width=2)
        for date, count in dates:
            if count % 10 == 1 and count // 10 != 1:
                suffix = ''
            elif 2 <= count % 10 <= 4 and count // 10 != 1:
                suffix = 'а'
            else:
                suffix = 'ов'
            markup.add(telebot.types.InlineKeyboardButton(
                text=f"{date.strftime('%d.%m.%Y')} "
                     f"({weekdays_mapping[date.weekday()]}), "
                     f"{count} вариант{suffix}",
                callback_data=f'day|{date}|{call.data}')
            )

        bot.send_message(call.message.chat.id, f'Вы выбрали услугу: {name} \n'
                                               f'Ориентировочное время: {30 * duration} минут, \n'
                                               f'Выберите дату:',
                         reply_markup=markup)
        bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith('day|'))
def select_time(call):
    data = call.data.split('|')
    date = datetime.strptime(data[1], '%Y-%m-%d').date()
    name = data[2]
    duration = code_to_duration[name]

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""select to_char(datetime, 'HH24:MI')
from slots
where date(datetime) = %s
  and check_time(datetime, %s)
  and check_gap(datetime, %s)
order by datetime""", (date, duration, duration))

    times = cur.fetchall()
    markup = telebot.types.InlineKeyboardMarkup()
    for time in times:
        markup.add(telebot.types.InlineKeyboardButton(text=time[0], callback_data=f'conf|{time[0]}_{date}|{name}'))

    cur.close()
    conn.close()

    bot.send_message(call.message.chat.id,
                     text=f"Вы выбрали дату: {date.strftime('%d.%m.%Y')} ({weekdays_mapping[date.weekday()]})\n"
                          f"Выберите время:",
                     reply_markup=markup)

    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith('conf|'))
def confirm(call):
    data = call.data.split('|')
    time = datetime.strptime(data[1], '%H:%M_%Y-%m-%d')
    name = data[2]

    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(
        telebot.types.InlineKeyboardButton(text='Все верно!', callback_data=call.data.replace('conf', 'book')),
        telebot.types.InlineKeyboardButton(text='Что-то не так...', callback_data='reset')
    )

    bot.send_message(call.message.chat.id,
                     f'Пожалуйста, подтвердите правильность данных\n'
                     f'Услуга: {code_to_name[name]}\n'
                     f'Дата и время: {time.strftime("%d.%m.%Y")} '
                     f'({weekdays_mapping[time.weekday()]}) в {time.strftime("%H:%M")}',
                     reply_markup=markup)

    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith('book|'))
def book(call):
    data = call.data.split('|')
    time = datetime.strptime(data[1], '%H:%M_%Y-%m-%d')
    code = data[2]
    name = code_to_name[code]
    duration = code_to_duration[code]

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
    select count(*) from slots
    where %s <= datetime and
    datetime < (%s + interval '30 minutes' * %s) and
    is_booked = false;
    """, (time, time, duration))
    check = cur.fetchall()
    if check[0][0] != duration:
        bot.answer_callback_query(call.id, text='Ошибка! Попробуйте ещё раз.')
    else:
        cur.execute("""
        update slots set is_booked = true
        where %s <= datetime and
        datetime < (%s + interval '30 minutes' * %s) and
        is_booked = false;
        """, (time, time, duration))

        cur.execute("""
        insert into appointments(user_id, start_time, end_time, comment)
        values (%s, %s, %s + interval '30 minutes' * %s, %s);
        """, (call.from_user.id, time, time, duration, name))

        bot.answer_callback_query(call.id, text='Вы успешно записались!')
        conn.commit()

        for admin_id in admin_ids:
            bot.send_message(admin_id, text=f'@{call.from_user.username} записался на \n'
                                            f'{name} на {time.strftime("%d.%m.%Y %H:%M")}.')
        bot.send_message(call.from_user.id, text=f'Вы успешно записались на {name} \n'
                                                 f'на {time.strftime("%d.%m.%Y %H:%M")}.')
    cur.close()
    conn.close()


@bot.message_handler(func=lambda message: message.text == 'Мои записи' or message.text == '/my_appointments')
def my_appointments(message):
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
    select id, start_time from appointments where user_id = %s and start_time > now();
    """, (message.from_user.id,))

    appointments = cur.fetchall()
    if len(appointments) == 0:
        bot.send_message(message.chat.id, "У Вас нет предстоящих записей")

    else:
        markup = telebot.types.InlineKeyboardMarkup()
        for app_id, start_time in appointments:
            markup.add(telebot.types.InlineKeyboardButton(text=start_time.strftime('%d.%m.%Y %H:%M'),
                                                          callback_data=f'my_app|{app_id}'))

        bot.send_message(message.chat.id, "Выберите запись чтобы просмотреть услугу или отменить запись:",
                         reply_markup=markup)

    cur.close()
    conn.close()


@bot.callback_query_handler(func=lambda call: call.data.startswith('my_app|'))
def change_appointment(call):
    app_id = call.data.split('|')[1]
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
    select start_time, comment from appointments where id = %s;
    """, (app_id,))

    start_time, comment = cur.fetchall()[0]
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(
        telebot.types.InlineKeyboardButton(text='Отменить запись', callback_data=f'cancel|{app_id}'),
        telebot.types.InlineKeyboardButton(text='Вернуться в главное меню', callback_data='reset')
    )

    bot.send_message(call.message.chat.id,
                     text=f"{start_time.strftime('%d.%m.%Y')} ({weekdays_mapping[start_time.weekday()]}) в {start_time.strftime('%H:%M')} "
                          f"Вы записаны на \n {comment}",
                     reply_markup=markup)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith('cancel|'))
def cancel_appointment(call):
    app_id = call.data.split('|')[1]

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
    select start_time, end_time, user_id from appointments where id = %s;
    """, (app_id,))
    data = cur.fetchone()
    if data is None:
        bot.answer_callback_query(call.id, text='Записи не существует.')

    else:
        start_time, end_time, user_id = data
        cur.execute('select username from users where id = %s', (user_id,))
        username = cur.fetchone()[0]

        cur.execute("""
        update slots set is_booked = false
        where %s <= datetime and
        datetime < %s and
        is_booked = true;
        """, (start_time, end_time))

        cur.execute("""
        delete from appointments where id = %s;
        """, (app_id,))

        bot.answer_callback_query(call.id, text='Запись отменена!')

        for admin_id in admin_ids:
            bot.send_message(admin_id, text=f'Запись у @{username} на {start_time.strftime("%d.%m.%Y %H:%M")} отменена.')
        bot.send_message(user_id, text=f'Ваша запись на {start_time.strftime("%d.%m.%Y %H:%M")} отменена.')

        conn.commit()
    cur.close()
    conn.close()


@bot.callback_query_handler(func=lambda call: call.data == 'show_apps')
def show_apps(call):
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
    select d.day, count(a.id) as appointments_count
    from days d
    left join appointments a
    on date(a.start_time) = d.day
    where d.day >= current_date and d.is_working=true
    group by d.day
    order by d.day;
    """)

    data = cur.fetchall()

    cur.close()
    conn.close()
    if len(data) == 0:
        bot.answer_callback_query(call.id, text='Нет предстоящих записей')
    else:
        markup = telebot.types.InlineKeyboardMarkup()
        for date, count in data:
            if count % 10 == 1 and count // 10 != 1:
                suffix = 'ь'
            elif 2 <= count % 10 <= 4 and count // 10 != 1:
                suffix = 'и'
            else:
                suffix = 'ей'
            markup.add(telebot.types.InlineKeyboardButton(
                text=f"{date.strftime('%d.%m.%Y')} ({weekdays_mapping[date.weekday()]}), {count} запис{suffix}",
                callback_data=f'show_day|{date}'))

        bot.send_message(call.message.chat.id, text='Выберите день чтобы посмотреть список записей', reply_markup=markup)
        bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith('show_day|'))
def show_day(call):
    conn = get_db_connection()
    cur = conn.cursor()

    data = call.data.split('|')
    date = datetime.strptime(data[1], '%Y-%m-%d').date()

    cur.execute("""
    select a.id as appointment_id, u.username, 
           a.start_time, a.end_time, a.comment
    from appointments a
    join users u
    on a.user_id = u.id
    where date(start_time) = %s
    order by a.start_time
""", (date,))

    data = cur.fetchall()

    cur.close()
    conn.close()
    if len(data) == 0:
        bot.answer_callback_query(call.id, 'В этот день записей нет')
    else:
        markup = telebot.types.InlineKeyboardMarkup(row_width=2)
        s = f'Ваши записи на {date.strftime("%d.%m.%Y")}: \n'
        for app_id, username, start_time, end_time, comment in data:
            s += (f'{start_time.strftime("%H:%M")}-{end_time.strftime("%H:%M")} у @{username}:\n'
                  f'{comment}\n')
            markup.add(telebot.types.InlineKeyboardButton(text=start_time.strftime("%H:%M"), callback_data=f'my_app|{app_id}'))

        s += '\nКликните на время чтобы отменить запись\n'

        bot.send_message(call.message.chat.id, text=s, reply_markup=markup)
        bot.answer_callback_query(call.id)


code_to_name = {'sel_manic_short_reinf_des': 'Маникюр с покрытием, короткие ногти, с укреплением и дизайном',
                'sel_manic_short_reinf_nodes': 'Маникюр с покрытием, короткие ногти, с укреплением, без дизайна',
                'sel_manic_short_noreinf_des': 'Маникюр с покрытием, короткие ногти, без укрепления, с дизайном',
                'sel_manic_short_noreinf_nodes': 'Маникюр с покрытием, короткие ногти, без укрепления, без дизайна',
                'sel_manic_long_des': 'Маникюр с покрытием, длинные ногти, с дизайном',
                'sel_manic_long_sim': 'Маникюр с покрытием, длинные ногти, без дизайна',
                'sel_ext_long_des': 'Наращивание, длинные ногти, с дизайном',
                'sel_ext_long_sim': 'Наращивание, длинные ногти, без дизайном',
                'sel_ext_short_des': 'Наращивание, короткие ногти, с дизайном',
                'sel_ext_short_sim': 'Наращивание, короткие ногти, без дизайна',
                'sel_ext_cor_long_des': 'Коррекция наращивания, длинные ногти, с дизайном',
                'sel_ext_cor_long_sim': 'Коррекция наращивания, длинные ногти, без дизайна',
                'sel_ext_cor_short_des': 'Коррекция наращивания, короткие ногти, с дизайном',
                'sel_ext_cor_short_sim': 'Коррекция наращивания, короткие ногти, без дизайна',
                'sel_ped_coat_heel': 'Педикюр, с покрытием, пятка и пальчики',
                'sel_ped_heel': 'Педикюр, без покрытия, пятка и пальчики',
                'sel_ped_coat_finger': 'Педикюр, с покрытием, только пальчики',
                'sel_ped_finger': 'Педикюр, только пальчики',
                'sel_manic_sim': 'Маникюр без покрытия'}

code_to_duration = {'sel_manic_short_reinf_des': 5,
                    'sel_manic_short_reinf_nodes': 5,
                    'sel_manic_short_noreinf_des': 4,
                    'sel_manic_short_noreinf_nodes': 4,
                    'sel_manic_long_des': 6,
                    'sel_manic_long_sim': 5,
                    'sel_ext_long_des': 8,
                    'sel_ext_long_sim': 8,
                    'sel_ext_short_des': 7,
                    'sel_ext_short_sim': 7,
                    'sel_ext_cor_long_des': 6,
                    'sel_ext_cor_long_sim': 5,
                    'sel_ext_cor_short_des': 5,
                    'sel_ext_cor_short_sim': 5,
                    'sel_ped_coat_heel': 5,
                    'sel_ped_heel': 3,
                    'sel_ped_coat_finger': 4,
                    'sel_ped_finger': 2,
                    'sel_manic_sim': 2}

# Запуск бота
if __name__ == "__main__":
    init_db()
    bot.polling(non_stop=True)
