import pandas as pd
import numpy as np
from bs4 import BeautifulSoup
import requests
import json
import re
import telebot
from telebot import types


GOOD_RESPONSE_STATUS = 200


class DishSplitter:

    def __init__(
        self,
        yandex_translate_key,
        usda_key,
        rel_url,
        mass
    ):
        try:
            self.table = self._parse_dish(
                yandex_translate_key,
                usda_key,
                rel_url,
                mass
            )
        except Exception as e:
            raise e

    @staticmethod
    def from_config(
        json_path: str,
        rel_url: str,
        mass: int
    ):
        # загрузка API ключей из json файла
        with open(json_path, 'r') as json_file:
            api_keys = json.load(json_file)

        yandex_translate_key = api_keys['yandex_translate']
        usda_key = api_keys['usda']

        # создание объекта класса, который будет использоваться для передачи данных о блюде пользователю
        return DishSplitter(
            yandex_translate_key,
            usda_key,
            rel_url,
            mass / 100
        )

    def __call__(self) -> pd.DataFrame:
        # возвращает таблицу с поэлементным составом блюда
        return pd.DataFrame(self.table, columns=['Название', 'Масса', 'Белки', 'Жиры', 'Углеводы', 'ккал'])

    def _parse_dish(self, yandex_translate_key: str, usda_key: str, rel_url: str, mass: int) -> zip:
        '''
            По API ключам, относительному адресу блюда на eda.ru и массе 
            возвращает данные (белки, жиры, углеводы, ккал) по каждому из ингридиентов блюда
        '''

        # запрос по ранее проверенной ссылке на eda.ru
        recipe_url = f'https://eda.ru{rel_url}'

        recipe_response = requests.get(recipe_url, timeout=20)
        recipe_soup = BeautifulSoup(recipe_response.content, 'html.parser')

        # получение списка ингридинтов и их количества в блюде
        ingredient_soup = recipe_soup.body.find_all(
            'div', 'ingredients-list__content')[0].p

        labels = []
        amounts = []
        portions = []

        while ingredient_soup is not None:
            ingredient_data = ingredient_soup['data-ingredient-object']
            data_dict = json.loads(ingredient_data)
            labels.append(data_dict['name'])
            amounts.append(data_dict['amount'])
            ingredient_soup = ingredient_soup.next_sibling.next_sibling

        # договоримся, что:
        # один овощь в среднем весит 60 г,
        # в банке 200 г,
        # а "по вкусу" соответствует 5 г

        # привидение названий ингридиентов и их массы к удобному для анализа виду
        e = r'[\d%]'
        for i, s in enumerate(labels):
            labels[i] = re.sub(e, '', s).strip()

        e1 = r'по вкусу'
        e2 = r'\d* г'
        e3 = r'\d* мл'
        e4 = r'\d* литр.*'
        e5 = r'\d* штук.*'
        e6 = r'\d* чайн.* ложк.*'
        e7 = r'\d* стол.* ложк.*'
        e8 = r'\d* банк.*'
        e9 = r'\d* кус.*'

        for i, s in enumerate(amounts):
            if re.match(e1, s):
                res = 5
            elif re.match(e2, s) or re.match(e3, s):
                q = re.sub(',', '.', re.findall(r'\d*', s)[0])
                if q == '':
                    q = 0.5
                res = float(q)
            elif re.match(e4, s):
                q = re.sub(',', '.', re.findall(r'\d*', s)[0])
                if q == '':
                    q = 0.5
                res = float(q) * 1000
            elif re.match(e5, s) or re.match(e9, s):
                q = re.sub(',', '.', re.findall(r'\d*', s)[0])
                if q == '':
                    q = 0.5
                res = float(q) * 60
            elif re.match(e6, s):
                q = re.sub(',', '.', re.findall(r'\d*', s)[0])
                if q == '':
                    q = 0.5
                res = float(q) * 5
            elif re.match(e7, s):
                q = re.sub(',', '.', re.findall(r'\d*', s)[0])
                if q == '':
                    q = 0.5
                res = float(q) * 20
            else:
                q = re.sub(',', '.', re.findall(r'\d*', s)[0])
                if q == '':
                    q = 0.5
                res = float(q) * 200

            amounts[i] = round(res, 2)

        # вычисление количества каждого ингридиента для получения 100 г блюда
        overall_amount = round(sum(amounts), 2)
        amounts = list(
            map(lambda x: round(x / overall_amount * 100 * mass), amounts))

        # перевод на английский названий ингридиентов с использованием Yandex Translate API
        # и получение их состава с использование USDA API
        proteins = []
        fats = []
        carbohydrates = []
        kcals = []

        translate_url = 'https://translate.api.cloud.yandex.net/translate/v2/translate'
        headers = {'Authorization': f'Api-Key {yandex_translate_key}',
                   'Content-Type': 'application/json'}

        for i, label in enumerate(labels):
            translate_response = requests.post(translate_url, headers=headers, json={
                                               'texts': label, 'targetLanguageCode': 'en'})

            query = translate_response.json()['translations'][0]['text']

            nutrients_url = f'https://api.nal.usda.gov/fdc/v1/foods/search?query={query}&pageSize=2&api_key={usda_key}'

            nutrients_response = requests.get(nutrients_url, timeout=20)
            if nutrients_response.status_code != GOOD_RESPONSE_STATUS:
                protein = 0
                fat = 0
                carbohydrate = 0
                kcal = 0
                labels[i] = 'Неизвестный продукт'
            else:
                nutrients_dict = nutrients_response.json()[
                    'foods'][0]['foodNutrients']
                protein = round(
                    float(nutrients_dict[0]['value']) * amounts[i] / 100, 2)
                fat = round(
                    float(nutrients_dict[1]['value']) * amounts[i] / 100, 2)
                carbohydrate = round(
                    float(nutrients_dict[2]['value']) * amounts[i] / 100, 2)
                kcal = round(
                    float(nutrients_dict[3]['value']) * amounts[i] / 100, 2)
            proteins.append(protein)
            fats.append(fat)
            carbohydrates.append(carbohydrate)
            kcals.append(kcal)

        return zip(labels, amounts, proteins, fats, carbohydrates, kcals)


# получение API ключа телеграм бота
json_path = './api_keys.json'

with open(json_path, 'r') as json_file:
    api_keys = json.load(json_file)

TELEBOT_API_KEY = api_keys['telebot']

# инициализация бота
bot = telebot.TeleBot(TELEBOT_API_KEY)

# переменные, нужные для расчета и общения с пользователем
query = ''
mass = 0

first_dish = ''
second_dish = ''
third_dish = ''

first_dish_url = ''
second_dish_url = ''
third_dish_url = ''
relative_url = ''

restart_flag = False


def check_dishname(dish: str) -> bool:
    '''
        Функция проверяет существование рецепта 
        введенного блюда на eda.ru
    '''

    url = f'https://eda.ru/recipesearch?q={dish}'
    dish_response = requests.get(url, timeout=20)
    if dish_response.status_code != GOOD_RESPONSE_STATUS:
        print('Bad input')
        return False
    dish_soup = BeautifulSoup(dish_response.content, 'html.parser')

    try:
        rel_url = dish_soup.body.find_all('div', 'wrapper-sel')[0]\
            .find_all('section', ['main-content', 'layout__container', 'js-main-content'])[0]\
            .find_all('section', ['recipes-page', '_no-top-pad-search', 'layout__content'])[0]\
            .find_all('div', ['g-relative', 'recipes-page__recipes', 'sticky-content-container'])[0]\
            .find_all('div', ['tile-list', 'layout__content-col', 'widget-list_search', 'js-load-more-content'])[0]\
            .find_all('div', 'clearfix')[0]\
            .div.div['data-href']
    except:
        return False

    return True


@bot.message_handler(content_types=['text', 'document'])
def start(message):
    '''
        Стартовое состояние бота
        При вводе пользователем /help выводим подсказку
        При вводе /start начинается общение с пользователем
    '''

    if message.text == '/help':
        bot.send_message(message.from_user.id,
                         'Чтобы получить бжу показатели ингредиентов блюда, напишите /start')
    elif message.text == '/start':
        bot.send_message(message.from_user.id, 'Напишите название блюда')
        bot.register_next_step_handler(message, get_dish)
    else:
        bot.send_message(message.from_user.id, 'Напишите /help')


def get_dish(message):
    '''
        Функция помогает выбрать пользователю блюдо по названию из доступных
    '''

    # введенное пользователем название блюда
    global query
    query = message.text

    # проверка на существование введенного блюда
    if not check_dishname(query):
        bot.send_message(
            message.from_user.id, 'Блюдо не найдено, попробуйте проверить на опечатки и спросить снова')
        bot.register_next_step_handler(message, get_dish)
        return

    # посылаем запрос на сайт, чтобы предложить пользователю выбор из 3 блюд
    url = f'https://eda.ru/recipesearch?q={query}'
    menu_response = requests.get(url, timeout=20)
    menu_soup = BeautifulSoup(menu_response.content, 'html.parser')

    # получаем первые 3 блюда (их название и относительную ссылку на сайте)
    global first_dish, second_dish, third_dish
    global first_dish_url, second_dish_url, third_dish_url

    item = menu_soup.find_all('div', 'tile-list__horizontal-tile horizontal-tile js-portions-count-parent js-bookmark__obj')[0]\
        .find_all('div', 'horizontal-tile__content')[0]
    first_dish = re.sub('\xa0', ' ', item.h3.span.text.strip())
    first_dish_url = item.h3.a['href']

    item = menu_soup.find_all('div', 'tile-list__horizontal-tile horizontal-tile js-portions-count-parent js-bookmark__obj')[1]\
        .find_all('div', 'horizontal-tile__content')[0]
    second_dish = re.sub('\xa0', ' ', item.h3.span.text.strip())
    second_dish_url = item.h3.a['href']

    item = menu_soup.find_all('div', 'tile-list__horizontal-tile horizontal-tile js-portions-count-parent js-bookmark__obj')[2]\
        .find_all('div', 'horizontal-tile__content')[0]
    third_dish = re.sub('\xa0', ' ', item.h3.span.text.strip())
    third_dish_url = item.h3.a['href']

    # предлагаем пользователю выбрать из 3 опций, нажав на одну из трех кнопок
    keyboard = types.InlineKeyboardMarkup()

    key_first_dish = types.InlineKeyboardButton(
        text=first_dish, callback_data='first')
    keyboard.add(key_first_dish)

    key_second_dish = types.InlineKeyboardButton(
        text=second_dish, callback_data='second')
    keyboard.add(key_second_dish)

    key_third_dish = types.InlineKeyboardButton(
        text=third_dish, callback_data='third')
    keyboard.add(key_third_dish)

    key_exit = types.InlineKeyboardButton(
        text='Попробовать сначала', callback_data='exit')
    keyboard.add(key_exit)

    question = 'Выберите нужное блюдо'
    bot.send_message(message.from_user.id, text=question,
                     reply_markup=keyboard)

    # обрабатываем дальнейшее действие получения массы
    global restart_flag
    if not restart_flag:
        bot.register_next_step_handler(message, get_mass)
    restart_flag = False


@bot.callback_query_handler(func=lambda call: True)
def callback_worker(call):
    '''
        Функция обработчик события нажатия на кнопку
    '''

    global restart_flag
    global query, first_dish, second_dish, third_dish
    global relative_url, first_dish_url, second_dish_url, third_dish_url

    if call.data == 'first':
        query = first_dish
        relative_url = first_dish_url
    elif call.data == 'second':
        query = second_dish
        relative_url = second_dish_url
    elif call.data == 'third':
        query = third_dish
        relative_url = third_dish_url
    elif call.data == 'exit':
        bot.send_message(call.message.chat.id, 'Напишите название блюда')
        bot.register_next_step_handler(call.message, get_dish)
        restart_flag = True
        return
    else:
        bot.send_message(call.message.chat.id,
                         'Что-то пошло не так, попробуйте еще раз')
        bot.register_next_step_handler(call.message, get_dish)
        restart_flag = True
        return

    # дальнейшее взаимодействие с пользователем
    bot.send_message(call.message.chat.id, 'Напишите массу в граммах')


def get_mass(message):
    '''
        Функция получает массу и выдает пользователю результат в виде excel таблицы
    '''

    # получение массы от пользователя
    global restart_flag
    if restart_flag:
        return
    global mass
    try:
        mass = float(message.text)
    except Exception:
        bot.send_message(message.from_user.id, 'Цифрами, пожалуйста')
        bot.register_next_step_handler(message, get_mass)
        return

    # инициализация объекта класса
    bot.send_message(message.from_user.id, 'Начинаю расчеты...')
    global relative_url
    try:
        dish_splitter = DishSplitter.from_config(
            json_path='./api_keys.json',
            rel_url=relative_url,
            mass=mass
        )
    except:
        bot.send_message(
            message.from_user.id, 'Что-то пошло не так на стороне сервиса. Попробуйте начать сначала')
        bot.register_next_step_handler(message, get_dish)
        return

    # работа с таблицей
    df = dish_splitter()
    ds = pd.Series({
        'Название': 'Итог',
        'Масса': mass,
        'Белки': round(df['Белки'].sum(), 2),
        'Жиры': round(df['Жиры'].sum(), 2),
        'Углеводы': round(df['Углеводы'].sum(), 2),
        'ккал': round(df['ккал'].sum(), 2),
    })
    df = df.append(pd.Series(), ignore_index=True).append(
        ds, ignore_index=True)
    df.to_excel(f'{query}.xlsx', index=False)

    # отправка результата пользователю
    bot.send_message(message.from_user.id,
                     'Расчеты закончены, высылаю таблицу')
    with open(f'{query}.xlsx', 'rb') as file:
        bot.send_document(message.from_user.id, file)

    # ожидание следующего названия блюда
    mass = 0
    bot.send_message(message.from_user.id,
                     'Напишите название слюдующего блюда')
    bot.register_next_step_handler(message, get_dish)


bot.polling(none_stop=True, interval=0)

