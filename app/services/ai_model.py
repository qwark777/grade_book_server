"""
Простая ML модель для анализа успеваемости и генерации AI-советов
"""
from typing import List, Dict, Tuple
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
import joblib
import os

class StudentPerformanceAnalyzer:
    """Класс для анализа успеваемости студента с помощью ML"""
    
    def __init__(self):
        self.model = None
        self.scaler = StandardScaler()
        self.model_path = "models/advice_model.pkl"
        self.scaler_path = "models/advice_scaler.pkl"
        self._initialize_model()
    
    def _initialize_model(self):
        """Инициализация или загрузка модели"""
        # Создаем директорию для моделей
        os.makedirs("models", exist_ok=True)
        
        # Пытаемся загрузить сохраненную модель
        if os.path.exists(self.model_path) and os.path.exists(self.scaler_path):
            try:
                self.model = joblib.load(self.model_path)
                self.scaler = joblib.load(self.scaler_path)
                return
            except Exception:
                pass
        
        # Создаем новую модель (пока используем простой классификатор)
        # В будущем можно обучить на реальных данных
        self.model = RandomForestClassifier(
            n_estimators=50,
            max_depth=10,
            random_state=42,
            n_jobs=-1
        )
        
        # Обучаем на синтетических данных для старта
        self._train_on_synthetic_data()
    
    def _train_on_synthetic_data(self):
        """Обучение модели на синтетических данных (для старта)"""
        # Генерируем синтетические данные для обучения
        np.random.seed(42)
        n_samples = 1000
        
        # Признаки: [средний_балл, процент_пропусков, тренд, количество_предметов_с_низким_баллом]
        X = np.random.rand(n_samples, 4)
        X[:, 0] = X[:, 0] * 3 + 2  # Средний балл от 2 до 5
        X[:, 1] = X[:, 1] * 100    # Процент пропусков от 0 до 100
        X[:, 2] = X[:, 2] * 2 - 1  # Тренд от -1 до 1
        X[:, 3] = X[:, 3] * 5      # Количество проблемных предметов
        
        # Целевая переменная: приоритет совета (0=низкий, 1=средний, 2=высокий)
        y = np.zeros(n_samples, dtype=int)
        for i in range(n_samples):
            avg_score = X[i, 0]
            absence_rate = X[i, 1]
            trend = X[i, 2]
            problem_subjects = X[i, 3]
            
            if avg_score < 3.0 or absence_rate > 20 or trend < -0.3 or problem_subjects >= 3:
                y[i] = 2  # Высокий приоритет
            elif avg_score < 3.5 or absence_rate > 10 or trend < 0 or problem_subjects >= 2:
                y[i] = 1  # Средний приоритет
            else:
                y[i] = 0  # Низкий приоритет
        
        # Обучаем модель
        X_scaled = self.scaler.fit_transform(X)
        self.model.fit(X_scaled, y)
        
        # Сохраняем модель
        try:
            joblib.dump(self.model, self.model_path)
            joblib.dump(self.scaler, self.scaler_path)
        except Exception:
            pass  # Если не удалось сохранить, продолжаем работу
    
    def extract_features(
        self,
        grades: List[Dict],
        attendance_stats: Dict = None,
        performance_trends: List[Dict] = None
    ) -> np.ndarray:
        """Извлекает признаки из данных студента"""
        features = np.zeros(4)
        
        # 1. Средний балл
        if grades:
            total_score = sum(g.get('value', 0) for g in grades)
            features[0] = total_score / len(grades) if grades else 0
        else:
            features[0] = 0
        
        # 2. Процент пропусков
        if attendance_stats:
            total = attendance_stats.get('total_lessons', 0)
            absent = attendance_stats.get('absent', 0)
            features[1] = (absent / total * 100) if total > 0 else 0
        else:
            features[1] = 0
        
        # 3. Тренд (изменение среднего балла)
        if performance_trends and len(performance_trends) >= 2:
            first_score = performance_trends[0].get('average_score', 0)
            last_score = performance_trends[-1].get('average_score', 0)
            features[2] = last_score - first_score  # Положительное = улучшение, отрицательное = ухудшение
        else:
            features[2] = 0
        
        # 4. Количество предметов с низким средним баллом (< 3.5)
        if grades:
            from collections import defaultdict
            subject_scores = defaultdict(list)
            for grade in grades:
                subject = grade.get('subject', '')
                value = grade.get('value', 0)
                if subject:
                    subject_scores[subject].append(value)
            
            problem_count = sum(
                1 for scores in subject_scores.values()
                if scores and (sum(scores) / len(scores)) < 3.5
            )
            features[3] = problem_count
        else:
            features[3] = 0
        
        return features.reshape(1, -1)
    
    def predict_priority(
        self,
        grades: List[Dict],
        attendance_stats: Dict = None,
        performance_trends: List[Dict] = None
    ) -> Tuple[int, float]:
        """
        Предсказывает приоритет совета (0=низкий, 1=средний, 2=высокий)
        Возвращает (приоритет, уверенность)
        """
        if not self.model:
            # Fallback на правило-ориентированный подход
            return self._rule_based_priority(grades, attendance_stats, performance_trends)
        
        try:
            features = self.extract_features(grades, attendance_stats, performance_trends)
            features_scaled = self.scaler.transform(features)
            
            # Предсказание
            priority = self.model.predict(features_scaled)[0]
            
            # Вероятность предсказания (уверенность)
            probabilities = self.model.predict_proba(features_scaled)[0]
            confidence = probabilities[priority]
            
            return int(priority), float(confidence)
        except Exception:
            # Fallback на правило-ориентированный подход
            return self._rule_based_priority(grades, attendance_stats, performance_trends)
    
    def _rule_based_priority(
        self,
        grades: List[Dict],
        attendance_stats: Dict = None,
        performance_trends: List[Dict] = None
    ) -> Tuple[int, float]:
        """Fallback на правило-ориентированный подход"""
        if not grades:
            return 0, 0.5  # Низкий приоритет, если нет данных
        
        avg_score = sum(g.get('value', 0) for g in grades) / len(grades)
        absence_rate = 0
        if attendance_stats:
            total = attendance_stats.get('total_lessons', 0)
            absent = attendance_stats.get('absent', 0)
            absence_rate = (absent / total * 100) if total > 0 else 0
        
        trend = 0
        if performance_trends and len(performance_trends) >= 2:
            first = performance_trends[0].get('average_score', 0)
            last = performance_trends[-1].get('average_score', 0)
            trend = last - first
        
        # Определяем приоритет на основе правил
        if avg_score < 3.0 or absence_rate > 20 or trend < -0.3:
            return 2, 0.8  # Высокий
        elif avg_score < 3.5 or absence_rate > 10 or trend < 0:
            return 1, 0.7  # Средний
        else:
            return 0, 0.6  # Низкий

# Глобальный экземпляр анализатора
_analyzer = None

def get_analyzer() -> StudentPerformanceAnalyzer:
    """Получить экземпляр анализатора (singleton)"""
    global _analyzer
    if _analyzer is None:
        _analyzer = StudentPerformanceAnalyzer()
    return _analyzer

