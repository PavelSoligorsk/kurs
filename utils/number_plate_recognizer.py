from fast_alpr import ALPR
import asyncio
import logging
import cv2
import numpy as np
from typing import Optional, Tuple
from concurrent.futures import ThreadPoolExecutor
import re

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class NumberPlateRecognizer:
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=2)
        try:
            self.alpr = ALPR(
                detector_model="yolo-v9-t-384-license-plate-end2end",
                ocr_model="cct-xs-v2-global-model",
            )
            logger.info("ALPR initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize ALPR: {e}")
            self.alpr = None
    
    async def recognize_plate(self, image_data: bytes) -> Tuple[Optional[str], float]:
        """Асинхронное распознавание номера"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self.executor,
            self._recognize_plate_sync,
            image_data
        )
    
    def _recognize_plate_sync(self, image_data: bytes) -> Tuple[Optional[str], float]:
        """Синхронное распознавание номера"""
        if self.alpr is None:
            logger.error("ALPR not initialized")
            return None, 0.0
        
        try:
            # Конвертируем bytes в numpy array
            nparr = np.frombuffer(image_data, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            
            if img is None:
                logger.error("Failed to decode image")
                return None, 0.0
            
            # Распознаём
            results = self.alpr.predict(img)
            
            if results and len(results) > 0:
                result = results[0]
                
                # Получаем текст номера
                plate = result.ocr.text if hasattr(result, 'ocr') and result.ocr else None
                
                # Получаем уверенность: если список - берем среднее или минимум
                confidence = 0.0
                if hasattr(result, 'ocr') and result.ocr:
                    if isinstance(result.ocr.confidence, list):
                        # Берем минимальную уверенность по символам (консервативный подход)
                        confidence = min(result.ocr.confidence) if result.ocr.confidence else 0.0
                    else:
                        confidence = result.ocr.confidence
                
                # Логируем дополнительную информацию
                if hasattr(result.ocr, 'region') and result.ocr.region:
                    logger.info(f"Region detected: {result.ocr.region} (conf={result.ocr.region_confidence:.3f})")
                
                if plate and str(plate).strip():
                    plate_str = str(plate).strip().upper()
                    plate_str = self._format_plate(plate_str)
                    logger.info(f"Recognized: {plate_str} (min_confidence={confidence:.3f})")
                    return plate_str, float(confidence)
                else:
                    logger.warning("Empty plate text")
                    return None, 0.0
            
            logger.warning("No plate recognized")
            return None, 0.0
            
        except Exception as e:
            logger.error(f"Recognition error: {e}", exc_info=True)
            return None, 0.0
    
    def recognize_plate_from_file(self, image_path: str) -> Tuple[Optional[str], float]:
        """Распознавание из файла (синхронно)"""
        if self.alpr is None:
            return None, 0.0
        
        try:
            results = self.alpr.predict(image_path)
            
            if results and len(results) > 0:
                result = results[0]
                
                plate = result.ocr.text if hasattr(result, 'ocr') and result.ocr else None
                
                confidence = 0.0
                if hasattr(result, 'ocr') and result.ocr:
                    if isinstance(result.ocr.confidence, list):
                        confidence = min(result.ocr.confidence) if result.ocr.confidence else 0.0
                    else:
                        confidence = result.ocr.confidence
                
                if plate and str(plate).strip():
                    plate_str = self._format_plate(str(plate).strip().upper())
                    return plate_str, float(confidence)
            
            return None, 0.0
            
        except Exception as e:
            logger.error(f"File recognition error: {e}")
            return None, 0.0
    
    def draw_predictions(self, image_data: bytes) -> Optional[np.ndarray]:
        """Визуализация результатов"""
        if self.alpr is None:
            return None
        
        try:
            nparr = np.frombuffer(image_data, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            
            if frame is None:
                return None
            
            drawn = self.alpr.draw_predictions(frame)
            if hasattr(drawn, 'image'):
                return drawn.image
            elif isinstance(drawn, np.ndarray):
                return drawn
            return None
            
        except Exception as e:
            logger.error(f"Drawing error: {e}")
            return None
    
    def _format_plate(self, text: str) -> str:
        """Форматирование белорусского номера"""
        if not text or text == 'None':
            return text
        
        text = str(text).upper().strip()
        
        # Убираем лишние символы
        text = re.sub(r'[^A-Z0-9]', '', text)
        
        # Формат: 2222PT2 -> 2222 PT-2
        match = re.match(r'^(\d{4})([A-Z]{2})(\d+)$', text)
        if match:
            return f"{match.group(1)} {match.group(2)}-{match.group(3)}"
        
        # Формат с BY: BY2222PT2 -> BY-2222-PT-2
        match_by = re.match(r'^(BY)(\d{4})([A-Z]{2})(\d+)$', text)
        if match_by:
            return f"{match_by.group(1)}-{match_by.group(2)}-{match_by.group(3)}-{match_by.group(4)}"
        
        return text
    
    async def close(self):
        """Закрытие ресурсов"""
        self.executor.shutdown(wait=True)


# Глобальный экземпляр
plate_recognizer = NumberPlateRecognizer()