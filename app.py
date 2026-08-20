import os
from flask import Flask, request, jsonify
from flask_cors import CORS
from caldav import DAVClient
from icalendar import Calendar, Event
from datetime import datetime, timedelta, timezone
import uuid
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)


YANDEX_LOGIN = os.getenv('YANDEX_LOGIN')
YANDEX_APP_PASSWORD = os.getenv('YANDEX_APP_PASSWORD')
CALENDAR_ID = os.getenv('CALENDAR_ID', 'events-default')
CALDAV_URL = "https://caldav.yandex.ru"
WORK_START = 9   # 9:00
WORK_END = 18    # 18:00
SLOT_DURATION = 60  # минут

def get_calendar():
    """Подключается к Яндекс.Календарю и возвращает объект календаря."""
    client = DAVClient(
        url=CALDAV_URL,
        username=YANDEX_LOGIN,
        password=YANDEX_APP_PASSWORD
    )
    principal = client.principal()
    calendars = principal.calendars()
    for cal in calendars:
        if CALENDAR_ID in cal.id:
            return cal
    return None

def get_busy_slots(date):
    """Возвращает список занятых интервалов для указанной даты."""
    calendar_obj = get_calendar()
    if not calendar_obj:
        return []

    start_dt = datetime(date.year, date.month, date.day, 0, 0, 0, tzinfo=timezone.utc)
    end_dt = datetime(date.year, date.month, date.day, 23, 59, 59, tzinfo=timezone.utc)
    events = calendar_obj.date_search(
        start=start_dt,
        end=end_dt,
        expand=True
    )
    busy = []
    for event_data in events:
        try:
            cal = Calendar.from_ical(event_data)
            for component in cal.walk():
                if component.name == "VEVENT":
                    dtstart = component.get('dtstart').dt
                    dtend = component.get('dtend').dt
                    if dtstart.tzinfo is None:
                        dtstart = dtstart.replace(tzinfo=timezone.utc)
                    if dtend.tzinfo is None:
                        dtend = dtend.replace(tzinfo=timezone.utc)
                    busy.append((dtstart, dtend))
        except Exception:
            continue
    return busy

@app.route('/slots', methods=['GET'])
def get_free_slots():
    """Возвращает список свободных временных слотов для указанной даты."""
    date_str = request.args.get('date')
    if not date_str:
        return jsonify({"error": "Дата не указана"}), 400
    try:
        date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({"error": "Неверный формат даты"}), 400

    today = datetime.now().date()
    if date < today:
        return jsonify({"error": "Нельзя выбрать прошедшую дату"}), 400

    busy = get_busy_slots(date)

    slots = []
    start_dt = datetime(date.year, date.month, date.day, WORK_START, 0, 0, tzinfo=timezone.utc)
    end_dt = datetime(date.year, date.month, date.day, WORK_END, 0, 0, tzinfo=timezone.utc)
    current = start_dt
    while current + timedelta(minutes=SLOT_DURATION) <= end_dt:
        slot_start = current
        slot_end = current + timedelta(minutes=SLOT_DURATION)
        is_free = True
        for busy_start, busy_end in busy:
            if not (slot_end <= busy_start or slot_start >= busy_end):
                is_free = False
                break
        if is_free:
            slots.append(slot_start.strftime('%H:%M'))
        current += timedelta(minutes=SLOT_DURATION)

    return jsonify(slots)

@app.route('/book', methods=['POST'])
def book_appointment():
    data = request.json
    patient_name = data.get('name')
    patient_phone = data.get('phone')
    patient_email = data.get('email')
    start_time_str = data.get('start_time')

    if not patient_name or not patient_phone or not start_time_str:
        return jsonify({"error": "Не хватает данных"}), 400

    try:
        start_time = datetime.fromisoformat(start_time_str)
        end_time = start_time + timedelta(minutes=SLOT_DURATION)

        date = start_time.date()
        busy = get_busy_slots(date)
        for busy_start, busy_end in busy:
            if not (end_time <= busy_start or start_time >= busy_end):
                return jsonify({"error": "Это время уже занято"}), 409

        cal = Calendar()
        cal.add('prodid', '-//My Calendar//')
        cal.add('version', '2.0')

        event = Event()
        event.add('uid', str(uuid.uuid4()))
        event.add('dtstamp', datetime.now(timezone.utc))
        event.add('dtstart', start_time)
        event.add('dtend', end_time)
        event.add('summary', f'Консультация: {patient_name}')
        event.add('description', f'Телефон: {patient_phone}\nEmail: {patient_email or "Не указан"}')
        event.add('location', 'г. Екатеринбург, ул. Примерная, д. 1 (или онлайн)')

        cal.add_component(event)
        ics_data = cal.to_ical().decode('utf-8')

        calendar_obj = get_calendar()
        if not calendar_obj:
            return jsonify({"error": "Не удалось найти календарь"}), 500

        calendar_obj.save_event(ics_data)

        return jsonify({"success": True, "message": "Запись успешно создана!"}), 200

    except Exception as e:
        print(f"Ошибка: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
