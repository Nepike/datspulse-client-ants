import argparse
import os
import threading
import time

from matplotlib.backends._backend_tk import NavigationToolbar2Tk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.collections import PatchCollection
from matplotlib.lines import Line2D
from matplotlib.patches import RegularPolygon, Patch, Circle
from matplotlib.transforms import Affine2D
import math

import requests
import tkinter as tk
from tkinter import ttk, scrolledtext
import matplotlib
import matplotlib.pyplot as plt
import json
import numpy as np

# Конфигурация
BASE_URL = 'https://games-test.datsteam.dev/api'

try:
    import config
    HEADERS = {'accept': 'application/json', 'X-Auth-Token': config.TOKEN}
except ImportError:
    # без config.py остаётся только офлайн-режим на записанном ответе арены
    HEADERS = {'accept': 'application/json'}

# --offline: играть не по сети, а по снимку арены из fixtures/
OFFLINE = False
OFFLINE_ARENA = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             'fixtures', 'arena_turn19.json')

INF = 10**10
matplotlib.use('TkAgg')


class HexType:
    def __init__(self, color: str, name: str, cost: int, note: str):
        self.color = color
        self.name = name
        self.cost = cost
        self.note = note


HEX_TYPES = {1: HexType("#FF4500", "Муравейник", 1, ""),
             2: HexType("#F0F0F0", "Пустой", 1, ""),
             3: HexType("#D2B48C", "Грязь", 2, "Стоимость ОП увеличена"),
             4: HexType("#32CD32", "Кислота", 1, "Наносит 20 урона в конце хода"),
             5: HexType("#A9A9A9", "Камень", INF, "Непроходимый гекс")
}


class AntType:
    def __init__(self, color: str, name: str, health: int, damage: int, carry: int, see: int, speed: int, prob: int):
        self.color = color
        self.name = name
        self.health = health
        self.damage = damage
        self.carry = carry
        self.see = see
        self.speed = speed
        self.prob = prob


ANT_TYPES = {0: AntType(color="#4CAF50", name="Рабочий", health=130, damage=30, carry=8, see=1, speed=5, prob=60),
             1: AntType(color="#2196F3", name="Солдат", health=180, damage=70, carry=2, see=1, speed=4, prob=30),
             2: AntType(color="#9C27B0", name="Разведчик", health=80, damage=20, carry=2, see=4, speed=7, prob=10)
}


class FoodType:
    def __init__(self, color: str, name: str, saturation: int):
        self.color = color
        self.name = name
        self.saturation = saturation


FOOD_TYPES = {1: FoodType("#EF323D", "Яблоко", 10),
              2: FoodType("#8B4513", "Хлеб", 20),
              3: FoodType("#FFD700", "Нектар", 60),
              0: FoodType("#CCC", "Unknown", 0)}


class AntGameApp:
    def __init__(self, root):
        self.root = root
        self.root.title("DatsPulse Ants Game")
        self.root.geometry("1800x1000")
        self.root.state('zoomed')

        # Разметка
        self.top_frame = ttk.Frame(root, padding=10)
        self.top_frame.pack(side=tk.TOP, fill=tk.X)

        self.left_frame = ttk.Frame(root, width=350)
        self.left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=10)

        self.center_frame = ttk.Frame(root)
        self.center_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.right_frame = ttk.Frame(root, width=350)
        self.right_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=10, pady=10)

        # Данные игры
        self.fig, self.ax = plt.subplots(constrained_layout=True)
        self.logs = []
        self.game_data = {
            'ants': [],
            'enemies': [],
            'food': [],
            'home': [],
            'map': [],
            'score': 0,
            'turnNo': 0,
            'nextTurnIn': 0,
            'spot': {'q': 0, 'r': 0}
        }
        # Настройки карты
        self.first_update = True
        self.current_xlim = None
        self.current_ylim = None

        self.hex_patches = []
        self.ant_markers = []
        self.enemy_markers = []
        self.food_markers = []
        self.last_hex_size = 0.0

        self.selected_ant = None
        self.current_path = []
        self.planned_moves = {}
        self.path_arrows = []

        # Инициализация компонентов
        self.init_top_panel()
        self.init_left_panel()
        self.init_center_panel()
        self.init_right_panel()

        # Старт потока обновления данных
        self.update_thread = threading.Thread(target=self.update_game, daemon=True)
        self.update_thread.start()

    def init_top_panel(self):
        # Информация о ходе
        turn_frame = ttk.LabelFrame(self.top_frame, text="Ход игры", padding=10)
        turn_frame.pack(side=tk.LEFT, fill=tk.X, padx=5, pady=5, expand=True)

        self.turn_var = tk.StringVar()
        self.turn_var.set("Ожидание данных...")
        ttk.Label(turn_frame, textvariable=self.turn_var, font=("Arial", 12)).pack(anchor=tk.W)

        # Информация о счете
        score_frame = ttk.LabelFrame(self.top_frame, text="Счет команды", padding=10)
        score_frame.pack(side=tk.LEFT, fill=tk.X, padx=5, pady=5)

        self.score_var = tk.StringVar()
        self.score_var.set("0")
        ttk.Label(score_frame, textvariable=self.score_var, font=("Arial", 14, "bold")).pack()

        reg_frame = ttk.Frame(self.top_frame)
        reg_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=5, pady=5)
        ttk.Button(reg_frame, text="Зарегистрироваться", command=self.register).pack(fill=tk.X, pady=5)
        ttk.Button(reg_frame, text="Обновить логи", command=self.get_logs).pack(fill=tk.X)

    def init_left_panel(self):
        # Информационная панель
        info_frame = ttk.LabelFrame(self.left_frame, text="Окно информации", padding=10)
        info_frame.pack(fill=tk.X, pady=5)

        self.info_text = tk.Text(info_frame, height=10, width=46, font=("Arial", 11))
        self.info_text.pack(fill=tk.X)
        self.info_text.insert(tk.END, "*Наведитесь на объект для просмотра информации*")
        self.info_text.config(state=tk.DISABLED)

        # Журнал событий
        log_frame = ttk.LabelFrame(self.left_frame, text="Журнал событий", padding=10)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        self.log_text = scrolledtext.ScrolledText(log_frame, height=15, width=46, font=("Arial", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.config(state=tk.DISABLED)

    def init_right_panel(self):
        # Статистика колонии
        stats_frame = ttk.LabelFrame(self.right_frame, text="Статистика колонии", padding=12, width=460)
        stats_frame.pack(fill=tk.X, pady=5)

        style = ttk.Style()
        style.configure("Stats.TLabel", font=("Arial", 10))
        style.configure("StatsValue.TLabel", font=("Arial", 10, "bold"))

        # Муравьи
        ants_frame = ttk.Frame(stats_frame)
        ants_frame.pack(fill=tk.X, pady=5)

        self.worker_count_var = tk.StringVar(value="0")
        self.soldier_count_var = tk.StringVar(value="0")
        self.scout_count_var = tk.StringVar(value="0")

        ttk.Label(ants_frame, text="Муравьи:", style="Stats.TLabel").grid(row=0, column=0, columnspan=2, sticky=tk.W, padx=5, pady=(0, 5))

        ttk.Label(ants_frame, text="Рабочие:", style="Stats.TLabel").grid(row=1, column=0, sticky=tk.W, padx=5)
        ttk.Label(ants_frame, textvariable=self.worker_count_var, style="StatsValue.TLabel").grid(row=1, column=1, sticky=tk.W)

        ttk.Label(ants_frame, text="Солдаты:", style="Stats.TLabel").grid(row=2, column=0, sticky=tk.W, padx=5)
        ttk.Label(ants_frame, textvariable=self.soldier_count_var, style="StatsValue.TLabel").grid(row=2, column=1, sticky=tk.W)

        ttk.Label(ants_frame, text="Разведчики:", style="Stats.TLabel").grid(row=3, column=0, sticky=tk.W, padx=5)
        ttk.Label(ants_frame, textvariable=self.scout_count_var, style="StatsValue.TLabel").grid(row=3, column=1, sticky=tk.W)

        # Карта
        resources_frame = ttk.Frame(stats_frame)
        resources_frame.pack(fill=tk.X, pady=5)
        ttk.Label(resources_frame, text="Карта:", style="Stats.TLabel").grid(row=0, column=0, columnspan=2, sticky=tk.W, padx=5, pady=(0, 5))

        self.apple_var = tk.StringVar(value="0")
        self.bread_var = tk.StringVar(value="0")
        self.nectar_var = tk.StringVar(value="0")

        ttk.Label(resources_frame, text="Яблоки:", style="Stats.TLabel").grid(row=1, column=0, sticky=tk.W, padx=5)
        ttk.Label(resources_frame, textvariable=self.apple_var, style="StatsValue.TLabel").grid(row=1, column=1, sticky=tk.W)

        ttk.Label(resources_frame, text="Хлеб:", style="Stats.TLabel").grid(row=2, column=0, sticky=tk.W, padx=5)
        ttk.Label(resources_frame, textvariable=self.bread_var, style="StatsValue.TLabel").grid(row=2, column=1, sticky=tk.W)

        ttk.Label(resources_frame, text="Нектар:", style="Stats.TLabel").grid(row=3, column=0, sticky=tk.W, padx=5)
        ttk.Label(resources_frame, textvariable=self.nectar_var, style="StatsValue.TLabel").grid(row=3, column=1, sticky=tk.W)

        # Враги
        enemies_frame = ttk.Frame(stats_frame)
        enemies_frame.pack(fill=tk.X, pady=5)
        ttk.Label(enemies_frame, text="Враги:", style="Stats.TLabel").grid(row=0, column=0, columnspan=2, sticky=tk.W, padx=5, pady=(0, 5))

        self.enemy_workers = tk.StringVar(value="0")
        self.enemy_soldiers = tk.StringVar(value="0")
        self.enemy_scouts = tk.StringVar(value="0")

        ttk.Label(enemies_frame, text="Рабочие:", style="Stats.TLabel").grid(row=1, column=0, sticky=tk.W, padx=5)
        ttk.Label(enemies_frame, textvariable=self.enemy_workers, style="StatsValue.TLabel").grid(row=1, column=1,sticky=tk.W)

        ttk.Label(enemies_frame, text="Солдаты:", style="Stats.TLabel").grid(row=2, column=0, sticky=tk.W, padx=5)
        ttk.Label(enemies_frame, textvariable=self.enemy_soldiers, style="StatsValue.TLabel").grid(row=2, column=1,sticky=tk.W)

        ttk.Label(enemies_frame, text="Разведчики:", style="Stats.TLabel").grid(row=3, column=0, sticky=tk.W, padx=5)
        ttk.Label(enemies_frame, textvariable=self.enemy_scouts, style="StatsValue.TLabel").grid(row=3, column=1, sticky=tk.W)

        # Управление муравьями
        move_frame = ttk.LabelFrame(self.right_frame, text="Управление муравьями", padding=10)
        move_frame.pack(fill=tk.X, pady=5)

        btn_frame = ttk.Frame(move_frame)
        btn_frame.pack(fill=tk.X, pady=5)
        ttk.Button(btn_frame, text="Очистить путь", command=self.clear_path).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Отправить команду", command=self.send_commands).pack(side=tk.RIGHT, padx=2)

        # Ответ сервера
        response_frame = ttk.LabelFrame(self.right_frame, text="Последний ответ сервера", padding=10)
        response_frame.pack(fill=tk.X, pady=5)
        self.response_text = tk.Text(response_frame, height=20, width=50, font=("Arial", 10))
        self.response_text.pack(fill=tk.X)
        self.response_text.config(state=tk.DISABLED)

    def init_center_panel(self):
        map_frame = ttk.LabelFrame(self.center_frame, text="Карта местности", padding=10)
        map_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.ax.set_aspect('equal')
        self.ax.axis('off')
        self.canvas = FigureCanvasTkAgg(self.fig, master=map_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.canvas.mpl_connect('motion_notify_event', self.map_on_motion)
        self.canvas.mpl_connect('axes_leave_event', self.map_on_leave)
        self.canvas.mpl_connect('button_press_event', self.map_on_click)

        # Добавляем тулбар для навигации
        self.toolbar = NavigationToolbar2Tk(self.canvas, map_frame)
        self.toolbar.update()
        self.toolbar.pack(side=tk.BOTTOM, fill=tk.X)

    def get_logs(self):
        if OFFLINE:
            return
        try:
            response = requests.get(f"{BASE_URL}/logs", headers=HEADERS)
            if response.status_code == 200:
                self.logs = response.json()
                self.log_text.delete(1.0, tk.END)
                for log in self.logs:
                    self.log_text.insert(tk.END, f"{log['time']}: {log['message']}\n")
                # Прокрутка вниз
                self.log_text.see(tk.END)
        except Exception as e:
            self.log_text.insert(f"Ошибка журнала: {str(e)}")

    def register(self):
        if OFFLINE:
            self.update_info_text('Офлайн-режим: регистрация недоступна.')
            return
        try:
            response = requests.post(f"{BASE_URL}/register", headers=HEADERS)
            if response.status_code == 200:
                data = response.json()
                self.server_response = f"Регистрация успешна!\nИмя: {data['name']}"

                # Обновляем поле ответа
                self.response_text.config(state=tk.NORMAL)
                self.response_text.delete(1.0, tk.END)
                self.response_text.insert(tk.END, self.server_response)
                self.response_text.config(state=tk.DISABLED)
            else:
                self.server_response = f"Ошибка регистрации: {response.status_code}\n{response.text}"
                self.response_text.config(state=tk.NORMAL)
                self.response_text.delete(1.0, tk.END)
                self.response_text.insert(tk.END, self.server_response)
                self.response_text.config(state=tk.DISABLED)
        except Exception as e:
            self.server_response = f"Ошибка: {str(e)}"
            self.response_text.config(state=tk.NORMAL)
            self.response_text.delete(1.0, tk.END)
            self.response_text.insert(tk.END, self.server_response)
            self.response_text.config(state=tk.DISABLED)

    def get_arena(self):
        if OFFLINE:
            with open(OFFLINE_ARENA, encoding='utf-8') as f:
                self.game_data = json.load(f)
            return
        try:
            
            response = requests.get(f"{BASE_URL}/arena", headers=HEADERS)
            if response.status_code == 200:
                self.game_data = response.json()
            else:
                self.response_text.config(state=tk.NORMAL)
                self.response_text.delete(1.0, tk.END)
                self.response_text.insert(tk.END, f"Ошибка: {response.status_code} - {response.text}")
                self.response_text.config(state=tk.DISABLED)
        except Exception as e:
            self.response_text.config(state=tk.NORMAL)
            self.response_text.delete(1.0, tk.END)
            self.response_text.insert(tk.END, f"Ошибка соединения: {str(e)}")
            self.response_text.config(state=tk.DISABLED)

    def update_colony_stats(self):
        self.colony_stats = {
            'worker_count': 0,
            'soldier_count': 0,
            'scout_count': 0,
            'apples': 0,
            'bread': 0,
            'nectar': 0,
            'enemy_workers': 0,
            'enemy_soldiers': 0,
            'enemy_scouts': 0,
        }
        for ant in self.game_data['ants']:
            if ant['type'] == 0:
                self.colony_stats['worker_count'] += 1
            elif ant['type'] == 1:
                self.colony_stats['soldier_count'] += 1
            elif ant['type'] == 2:
                self.colony_stats['scout_count'] += 1

        for food in self.game_data['food']:
            if food['type'] == 1:
                self.colony_stats['apples'] += food['amount']
            elif food['type'] == 2:
                self.colony_stats['bread'] += food['amount']
            elif food['type'] == 3:
                self.colony_stats['nectar'] += food['amount']

        for enemy in self.game_data['enemies']:
            if enemy['type'] == 0:
                self.colony_stats['enemy_workers'] += 1
            elif enemy['type'] == 1:
                self.colony_stats['enemy_soldiers'] += 1
            elif enemy['type'] == 2:
                self.colony_stats['enemy_scouts'] += 1

        self.worker_count_var.set(str(self.colony_stats['worker_count']))
        self.soldier_count_var.set(str(self.colony_stats['soldier_count']))
        self.scout_count_var.set(str(self.colony_stats['scout_count']))

        self.apple_var.set(f"{self.colony_stats['apples']} ед.")
        self.bread_var.set(f"{self.colony_stats['bread']} ед.")
        self.nectar_var.set(f"{self.colony_stats['nectar']} ед.")

        self.enemy_workers.set(f"{self.colony_stats['enemy_workers']}")
        self.enemy_soldiers.set(f"{self.colony_stats['enemy_soldiers']}")
        self.enemy_scouts.set(f"{self.colony_stats['enemy_scouts']}")

    def adjust_aspect_ratio(self, min_x, max_x, min_y, max_y):
        width = max_x - min_x
        height = max_y - min_y

        # Рассчитываем желаемое соотношение
        target_ratio = 13 / 10
        current_ratio = width / height

        center_x = (min_x + max_x) / 2
        center_y = (min_y + max_y) / 2

        if current_ratio > target_ratio:
            # Ширина слишком большая - увеличиваем высоту
            new_height = width / target_ratio
            min_y = center_y - new_height / 2
            max_y = center_y + new_height / 2
        else:
            # Высота слишком большая - увеличиваем ширину
            new_width = height * target_ratio
            min_x = center_x - new_width / 2
            max_x = center_x + new_width / 2

        return min_x, max_x, min_y, max_y

    def draw_entities(self, entities, hex_size, entity_type):
        """Отрисовывает объекты с группировкой по координатам"""
        # Очистка предыдущих маркеров
        if entity_type == "ant":
            self.ant_markers = []
        elif entity_type == "enemy":
            self.enemy_markers = []
        elif entity_type == "food":
            self.food_markers = []
        # Группируем объекты по координатам
        grouped = {}
        for entity in entities:
            key = (entity['q'], entity['r'])
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(entity)

        # Отрисовываем каждую группу
        for (q, r), items in grouped.items():
            x = hex_size * 3 / 2 * q
            y = hex_size * np.sqrt(3) * (r + q / 2)

            count = len(items)
            radius = hex_size * 0.4  # Радиус для размещения объектов

            if count == 1:
                # Одиночный объект - рисуем в центре
                entity = items[0]
                marker = self.draw_single_entity(x, y, entity, entity_type)

                # Сохраняем маркер и данные объекта
                if entity_type == "ant":
                    self.ant_markers.append((marker, entity))
                elif entity_type == "enemy":
                    self.enemy_markers.append((marker, entity))
                elif entity_type == "food":
                    self.food_markers.append((marker, entity))
            else:
                # Несколько объектов - распределяем по окружности
                angle_step = 2 * math.pi / count
                for i, entity in enumerate(items):
                    angle = i * angle_step
                    dx = radius * math.cos(angle)
                    dy = radius * math.sin(angle)
                    marker = self.draw_single_entity(x + dx, y + dy, entity, entity_type)

                    # Сохраняем маркер и данные объекта
                    if entity_type == "ant":
                        self.ant_markers.append((marker, entity))
                    elif entity_type == "enemy":
                        self.enemy_markers.append((marker, entity))
                    elif entity_type == "food":
                        self.food_markers.append((marker, entity))

    def draw_single_entity(self, x, y, entity, entity_type):
        """Отрисовывает отдельный объект и возвращает маркер"""
        if entity_type == "ant":
            ant_type = ANT_TYPES[entity['type']]
            marker, = self.ax.plot(x, y, 'o', markersize=12,
                                   color=ant_type.color, markeredgecolor='black')
            return marker

        elif entity_type == "enemy":
            ant_type = ANT_TYPES[entity['type']]
            marker, = self.ax.plot(x, y, 's', markersize=12,
                                   color=ant_type.color, markeredgecolor='black')
            return marker

        elif entity_type == "food":
            food_type = FOOD_TYPES[entity['type']]
            marker, = self.ax.plot(x, y, '^', markersize=12,
                                   color=food_type.color, markeredgecolor='black')

            # Добавляем текст с количеством еды
            text = self.ax.text(
                x, y,
                str(entity['amount']),  # Отображаем количество еды
                fontsize=8,
                color='white',
                ha='center',  # Горизонтальное выравнивание по центру
                va='center',  # Вертикальное выравнивание по центру
                fontweight='bold',
                bbox=dict(  # Добавляем подложку для лучшей читаемости
                    boxstyle="circle,pad=0.3",
                    facecolor='black',
                    edgecolor='none',
                    alpha=0.2
                )
            )
            return marker

    def update_map(self):
        # Сохраняем текущий вид карты перед обновлением
        if not self.first_update:
            self.current_xlim = self.ax.get_xlim()
            self.current_ylim = self.ax.get_ylim()

        # Очищаем предыдущие гексы
        self.hex_patches = []
        self.path_arrows = []

        self.ax.clear()
        self.ax.set_aspect('equal')
        self.ax.axis('off')

        # Рассчитываем границы карты
        min_q, max_q, min_r, max_r = 0, 0, 0, 0
        if self.game_data['map']:
            q_values = [cell['q'] for cell in self.game_data['map']]
            r_values = [cell['r'] for cell in self.game_data['map']]
            min_q, max_q = min(q_values), max(q_values)
            min_r, max_r = min(r_values), max(r_values)

        # Динамический размер гекса
        hex_count = len(self.game_data['map'])
        if hex_count == 0:
            return

        width_in_hex = max(1, max_q - min_q + 1)
        height_in_hex = max(1, max_r - min_r + 1)
        area_factor = max(width_in_hex, height_in_hex)
        hex_size = 8.0 / (1 + area_factor ** 0.7)
        hex_size = max(0.2, min(1.0, hex_size))
        self.last_hex_size = hex_size  # Сохраняем размер гекса

        # Отрисовка гексов
        hex_patches = []
        for cell in self.game_data['map']:
            x = hex_size * 3 / 2 * cell['q']
            y = hex_size * np.sqrt(3) * (cell['r'] + cell['q'] / 2)

            hexagon = RegularPolygon(
                (x, y), numVertices=6, radius=hex_size,
                orientation=np.pi / 6,
                facecolor=HEX_TYPES[cell['type']].color,
                edgecolor='black', alpha=0.8
            )
            self.hex_patches.append((hexagon, cell))
            self.ax.add_patch(hexagon)
        self.ax.add_collection(PatchCollection(hex_patches, match_original=True))

        # Отрисовка муравейников (с выделением)
        home_patches = []
        for home in self.game_data['home']:
            x = hex_size * 3 / 2 * home['q']
            y = hex_size * np.sqrt(3) * (home['r'] + home['q'] / 2)
            home_patches.append(RegularPolygon(
                (x, y), numVertices=6, radius=hex_size * 1.1,
                orientation=np.pi / 6,
                facecolor=HEX_TYPES[1].color,
                edgecolor='red', linewidth=2, alpha=0.9
            ))
        self.ax.add_collection(PatchCollection(home_patches, match_original=True))

        # Отрисовка объектов с группировкой
        self.draw_entities(self.game_data['ants'], hex_size, "ant")
        self.draw_entities(self.game_data['enemies'], hex_size, "enemy")
        self.draw_entities(self.game_data['food'], hex_size, "food")

        # Устанавливаем границы отображения
        if hex_count > 0:
            margin = hex_size * 2
            min_x = hex_size * 3 / 2 * min_q
            min_y = hex_size * np.sqrt(3) * (min_r + min_q / 2)
            max_x = hex_size * 3 / 2 * max_q
            max_y = hex_size * np.sqrt(3) * (max_r + max_q / 2)

            # Корректируем соотношение сторон
            min_x, max_x, min_y, max_y = self.adjust_aspect_ratio(
                min_x - margin, max_x + margin,
                min_y - margin, max_y + margin
            )

            # Восстанавливаем настройки вида
            if not self.first_update and self.current_xlim and self.current_ylim:
                self.ax.set_xlim(self.current_xlim)
                self.ax.set_ylim(self.current_ylim)
            else:
                # Для первого запуска устанавливаем границы
                self.ax.set_xlim(min_x, max_x)
                self.ax.set_ylim(min_y, max_y)
                self.first_update = False

        # Добавляем легенду
        legend_elements = []

        # Гексы
        for hex_type in HEX_TYPES.values():
            legend_elements.append(Patch(
                facecolor=hex_type.color,
                edgecolor='black',
                label=hex_type.name
            ))

        # Муравьи (круги)
        for ant_type in ANT_TYPES.values():
            legend_elements.append(Line2D(
                [0], [0],
                marker='o',
                color='w',
                label=ant_type.name,
                markerfacecolor=ant_type.color,
                markersize=10,
                markeredgecolor='black'
            ))

        # Враги (квадраты)
        for ant_type in ANT_TYPES.values():
            legend_elements.append(Line2D(
                [0], [0],
                marker='s',
                color='w',
                label=f"{ant_type.name} (враг)",
                markerfacecolor=ant_type.color,
                markersize=10,
                markeredgecolor='black'
            ))

        # Еда (треугольники)
        for food_type in FOOD_TYPES.values():
            legend_elements.append(Line2D(
                [0], [0],
                marker='^',
                color='w',
                label=food_type.name,
                markerfacecolor=food_type.color,
                markersize=10,
                markeredgecolor='black'
            ))

        # Муравейник (специальный элемент)
        legend_elements.append(Line2D(
            [0], [0],
            marker='h',  # Шестиугольник
            color='w',
            label="Муравейник",
            markerfacecolor=HEX_TYPES[1].color,
            markersize=10,
            markeredgecolor='red'
        ))

        # Создаем легенду
        legend = self.ax.legend(
            handles=legend_elements,
            loc='upper right',
            fontsize='x-small',
            framealpha=0.7,
            title="Легенда",
            title_fontsize='small'
        )
        legend.get_frame().set_facecolor('white')
        legend.get_frame().set_edgecolor('gray')

        # Автоматическая подгонка макета
        self.fig.tight_layout()
        self.draw_paths()
        self.canvas.draw()

    def map_on_motion(self, event):
        """Обработчик движения мыши"""
        if event.inaxes != self.ax:
            return

        # Проверяем объекты в порядке их отрисовки (сверху вниз)
        # 1. Муравьи
        for marker, ant in self.ant_markers:
            if self.is_point_near_marker(event, marker):
                self.show_ant_info(ant)
                return

        # 2. Враги
        for marker, enemy in self.enemy_markers:
            if self.is_point_near_marker(event, marker):
                self.show_enemy_info(enemy)
                return

        # 3. Еда
        for marker, food in self.food_markers:
            if self.is_point_near_marker(event, marker):
                self.show_food_info(food)
                return

        # 4. Гексы
        for patch, cell in self.hex_patches:
            if patch.contains_point((event.x, event.y)):
                self.show_hex_info(cell)
                return

        # Если не нашли объект - показываем стандартное сообщение
        self.show_default_info()

    def map_on_leave(self, event):
        """Обработчик выхода мыши с холста"""
        self.show_default_info()

    def map_on_click(self, event):
        if event.inaxes != self.ax or event.button != 1:
            return

        # Найти ближайший гекс
        hex_coords = self.find_closest_hex(event.xdata, event.ydata)
        if not hex_coords:
            return

        q, r = hex_coords

        # Проверить, кликнули ли на муравья
        ant = self.find_ant_at_hex(q, r)
        if ant:
            self.select_ant(ant)
            return

        # Если муравей выбран, добавить точку в путь
        if self.selected_ant:
            self.add_to_path(q, r)

    def find_closest_hex(self, x, y):
        min_dist = float('inf')
        closest_hex = None

        for patch, cell in self.hex_patches:
            center = patch.xy
            dist = math.sqrt((x - center[0]) ** 2 + (y - center[1]) ** 2)
            if dist < min_dist:
                min_dist = dist
                closest_hex = (cell['q'], cell['r'])

        # Проверяем что размер гекса был установлен
        if not hasattr(self, 'last_hex_size') or self.last_hex_size <= 0:
            return None

        return closest_hex if min_dist < self.last_hex_size * 1.5 else None

    def find_ant_at_hex(self, q, r):
        for ant in self.game_data['ants']:
            if ant['q'] == q and ant['r'] == r:
                return ant
        return None

    def select_ant(self, ant):
        self.selected_ant = ant
        ant_id = ant['id']

        # Загрузить существующий путь или создать новый
        if ant_id in self.planned_moves:
            self.current_path = self.planned_moves[ant_id]
        else:
            self.current_path = [{'q': ant['q'], 'r': ant['r']}]

        self.draw_paths()
        self.update_info_text(f"Выбран муравей ({ANT_TYPES[ant['type']].name})\nID: {ant_id}")

    def add_to_path(self, q, r):
        if not self.current_path or not self.selected_ant:
            return

        # Проверить соседство
        last_hex = self.current_path[-1]
        if not self.are_adjacent(last_hex, {'q': q, 'r': r}):
            self.update_info_text("Ошибка: гексы должны быть соседними!")
            return

        # Проверить ограничение по скорости
        ant_type = ANT_TYPES[self.selected_ant['type']]
        if len(self.current_path) - 1 >= ant_type.speed:
            self.update_info_text(f"Превышена скорость муравья! Макс: {ant_type.speed} шагов")

        # Добавить точку в путь
        self.current_path.append({'q': q, 'r': r})
        self.planned_moves[self.selected_ant['id']] = self.current_path
        self.draw_paths()

    def are_adjacent(self, hex1, hex2):
        dq = hex1['q'] - hex2['q']
        dr = hex1['r'] - hex2['r']
        # Правильное условие для шестиугольной сетки
        return (dq == 1 and dr == 0) or \
               (dq == 1 and dr == -1) or \
               (dq == 0 and dr == 1) or \
               (dq == 0 and dr == -1) or \
               (dq == -1 and dr == 1) or \
               (dq == -1 and dr == 0)

    def draw_paths(self):
        # Удалить старые стрелки
        for arrow in self.path_arrows:
            arrow.remove()
        self.path_arrows = []

        # Отрисовать все пути
        for ant_id, path in self.planned_moves.items():
            self.draw_path(path, ant_id == self.selected_ant['id'] if self.selected_ant else False)

        # Отрисовать текущий путь
        if self.selected_ant and self.current_path:
            self.draw_path(self.current_path, True)

        self.canvas.draw_idle()

    def draw_path(self, path, is_selected):
        # Проверяем что есть выбранный муравей
        if is_selected and not self.selected_ant:
            return
        ant_type = ANT_TYPES[self.selected_ant['type']] if is_selected else None
        max_steps = ant_type.speed if ant_type else float('inf')

        for i in range(len(path) - 1):
            start = path[i]
            end = path[i + 1]

            # Найти центры гексов
            start_center = next((patch.xy for patch, cell in self.hex_patches
                                 if cell['q'] == start['q'] and cell['r'] == start['r']), None)
            end_center = next((patch.xy for patch, cell in self.hex_patches
                               if cell['q'] == end['q'] and cell['r'] == end['r']), None)

            if not start_center or not end_center:
                continue

            # Определить цвет
            color = 'green' if i < max_steps else 'red'
            arrow_size = 12 if is_selected else 8
            alpha = 1.0 if is_selected else 0.7

            # Нарисовать стрелку
            arrow = self.ax.annotate('',
                                     xy=end_center,
                                     xytext=start_center,
                                     arrowprops=dict(arrowstyle='->',
                                                     color=color,
                                                     lw=2,
                                                     alpha=alpha,
                                                     mutation_scale=arrow_size))
            self.path_arrows.append(arrow)

    def clear_path(self):
        if self.selected_ant:
            ant_id = self.selected_ant['id']
            if ant_id in self.planned_moves:
                del self.planned_moves[ant_id]
            self.current_path = [{'q': self.selected_ant['q'], 'r': self.selected_ant['r']}]
            self.draw_paths()

    def send_commands(self):
        if OFFLINE:
            self.update_info_text('Офлайн-режим: ходы не отправляются.')
            return

        moves = []
        for ant_id, path in self.planned_moves.items():
            # Пропустить начальную позицию
            if len(path) > 1:
                moves.append({
                    "ant": ant_id,
                    "path": path[1:]
                })

        if not moves:
            self.update_info_text("Нет команд для отправки!")
            return

        data = {"moves": moves}

        try:
            response = requests.post(f"{BASE_URL}/move", headers=HEADERS, json=data)
            self.response_text.config(state=tk.NORMAL)
            self.response_text.delete(1.0, tk.END)
            self.response_text.insert(tk.END, f"Статус: {response.status_code}\n")
            self.response_text.insert(tk.END, json.dumps(response.json(), indent=2))
            self.response_text.config(state=tk.DISABLED)

            # Сбросить состояние после отправки
            self.planned_moves = {}
            self.selected_ant = None
            self.current_path = []
            self.draw_paths()

        except Exception as e:
            self.response_text.config(state=tk.NORMAL)
            self.response_text.delete(1.0, tk.END)
            self.response_text.insert(tk.END, f"Ошибка: {str(e)}")
            self.response_text.config(state=tk.DISABLED)

    def is_point_near_marker(self, event, marker):
        """Проверяет, находится ли точка рядом с маркером"""
        # Получаем экранные координаты маркера
        xy_pixels = self.ax.transData.transform([marker.get_xydata()[0]])
        x_pixel, y_pixel = xy_pixels[0]

        # Получаем экранные координаты курсора
        x_event, y_event = event.x, event.y

        # Рассчитываем расстояние в пикселях
        distance = math.sqrt((x_event - x_pixel) ** 2 + (y_event - y_pixel) ** 2)

        # Чувствительность в пикселях (радиус вокруг маркера)
        sensitivity = 15

        return distance < sensitivity

    def show_ant_info(self, ant):
        """Отображает информацию о муравье"""
        ant_type = ANT_TYPES[ant['type']]
        info = (
            f"Муравей ({ant_type.name})\n"
            f"ID: {ant['id']}\n"
            f"Здоровье: {ant['health']}\n"
            f"Координаты: ({ant['q']}, {ant['r']})\n"
            f"Тип: {ant_type.name}\n"
            f"Урон: {ant_type.damage}\n"
            f"Ноша: {ant['food']} (Макс {ant_type.carry})"
        )
        self.update_info_text(info)

    def show_enemy_info(self, enemy):
        """Отображает информацию о враге"""
        ant_type = ANT_TYPES[enemy['type']]
        info = (
            f"Враг ({ant_type.name})\n"
            f"Здоровье: {enemy['health']}\n"
            f"Координаты: ({enemy['q']}, {enemy['r']})\n"
            f"Тип: {ant_type.name}\n"
            f"Урон: {ant_type.damage}\n"
            f"Ноша: {enemy['food']} (Макс {ant_type.carry})"
        )
        self.update_info_text(info)

    def show_food_info(self, food):
        """Отображает информацию о еде"""
        food_type = FOOD_TYPES[food['type']]
        info = (
            f"Еда ({food_type.name})\n"
            f"Координаты: ({food['q']}, {food['r']})\n"
            f"Количество: {food['amount']}\n"
            f"Сытность: {food_type.saturation}"
        )
        self.update_info_text(info)

    def show_hex_info(self, cell):
        """Отображает информацию о гексе"""
        hex_type = HEX_TYPES[cell['type']]
        info = (
            f"Гекс: ({cell['q']}, {cell['r']})\n"
            f"Тип: {hex_type.name}\n"
            f"Стоимость ОП: {hex_type.cost}\n"
            f"{hex_type.note}"
        )
        self.update_info_text(info)

    def show_default_info(self):
        """Показывает стандартное сообщение"""
        self.update_info_text("*Наведитесь на объект для просмотра информации*")

    def update_info_text(self, text):
        """Обновляет информационное окно"""
        self.info_text.config(state=tk.NORMAL)
        self.info_text.delete(1.0, tk.END)
        self.info_text.insert(tk.END, text)
        self.info_text.config(state=tk.DISABLED)

    def update_game(self):
        while True:
            self.get_arena()
            # self.get_logs()  # also updates
            # self.planned_moves = {}
            # self.selected_ant = None
            # self.current_path = []

            self.update_colony_stats()
            self.turn_var.set(f"Текущий ход: {self.game_data['turnNo']} | Следующий ход через: {self.game_data['nextTurnIn']:.1f} сек")
            self.score_var.set(f"{self.game_data['score']}")
            self.update_map()

            sleep_time = min(1.0, self.game_data.get('nextTurnIn', 1.0))
            time.sleep(sleep_time)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='Клиент DatsPulse: карта арены, ручная прокладка маршрутов, отправка ходов')
    parser.add_argument('--offline', action='store_true',
                        help='работать на записанном снимке арены из fixtures/, '
                             'без токена и без обращений к серверу')
    OFFLINE = parser.parse_args().offline

    root = tk.Tk()

    app = AntGameApp(root)
    root.mainloop()