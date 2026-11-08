import time
from pathlib import Path

import numpy as np
import torch
from loguru import logger
from ultralytics import YOLO

from core.interfaces import DetectionResult, IInferenceEngine


class OnnxYoloClient(IInferenceEngine):
    """Cliente de inferência compatível com pesos .pt e engines TensorRT (.engine).
    
    Suporta carregamento automático de .engine (TensorRT FP16) com fallback para .pt:
    - Se model_path="models/aion.pt", verifica se "models/Aion.engine" existe
    - Se .engine existe: usa ele (10-50x mais rápido)
    - Caso contrário: usa .pt (PyTorch)
    """

    def __init__(self, model_path: Path, imgsz: int = 640, conf_thresh: float = 0.20):
        self.imgsz = imgsz
        self.conf_thresh = conf_thresh
        self.device = "cuda:0" if torch.cuda.is_available() else "cpu"
        
        # ✅ OTIMIZAÇÃO: Detecção automática de .engine com fallback para .pt
        resolved_path = self._resolve_model_path(model_path)
        self.is_engine = str(resolved_path).endswith(".engine")

        logger.info(
            f"Carregando modelo ({resolved_path.suffix}) no dispositivo {self.device}..."
        )

        # Carrega wrapper Ultralytics
        self.yolo = YOLO(str(resolved_path))
        self.names = self.yolo.names

        # Se for modelo PyTorch (.pt), extrai o nn.Module para execução direta
        if not self.is_engine and hasattr(self.yolo.model, "to"):
            self.model = self.yolo.model.to(self.device)
            self.model.eval()
            try:
                self.model.fuse()
            except Exception:
                pass
            self.model.float()
        else:
            # Para .engine (TensorRT), usamos a execução nativa otimizada do wrapper
            self.model = None
            logger.success(
                "🚀 Carregado como engine compilada TensorRT de alta performance!"
            )

        # Warmup inicial
        dummy = np.zeros((self.imgsz, self.imgsz, 3), dtype=np.uint8)
        self.infer(dummy)
        logger.success("✓ Warmup de inferência concluído.")

        self.last_preprocess_ms = 0.0
        self.last_inference_ms = 0.0
        self.last_postprocess_ms = 0.0

    @staticmethod
    def _resolve_model_path(model_path: Path) -> Path:
        """Resolve caminho do modelo com preferência por .engine (TensorRT).
        
        Lógica de fallback automático:
        1. Se model_path termina com .pt:
           - Procura versão .engine com mesmo nome base (ex: aion.pt -> Aion.engine)
           - Se .engine existe e é válido: usa ele (10-50x mais rápido)
           - Caso contrário: usa .pt original
        2. Se model_path termina com .engine:
           - Usa diretamente (sem fallback)
        
        Args:
            model_path: Caminho original (.pt ou .engine)
        
        Returns:
            Path otimizado (.engine se disponível, .pt caso contrário)
        """
        model_path = Path(model_path)
        
        # Se já é .engine, usa diretamente
        if model_path.suffix == ".engine":
            if model_path.exists():
                return model_path
            logger.warning(
                f"[OTIMIZAÇÃO] Arquivo .engine especificado não encontrado: {model_path}"
            )
            # Tenta fallback para .pt
            pt_path = model_path.with_suffix(".pt")
            if pt_path.exists():
                logger.info(f"[OTIMIZAÇÃO] Usando fallback .pt: {pt_path}")
                return pt_path
            return model_path  # Retorna original (falhará no carregamento)
        
        # Se é .pt, procura versão .engine otimizada
        if model_path.suffix == ".pt":
            # Tenta encontrar .engine com capitalização correta (Aion.engine)
            stem_capitalized = model_path.stem.capitalize()
            engine_path = model_path.parent / f"{stem_capitalized}.engine"
            
            if engine_path.exists():
                logger.success(
                    f"🚀 [OTIMIZAÇÃO] Detectado .engine otimizado: {engine_path.name}"
                )
                logger.info(
                    f"    └─ Usando TensorRT FP16 (10-50x mais rápido que .pt)"
                )
                return engine_path
            
            # Fallback: usa .pt original
            if model_path.exists():
                logger.info(
                    f"[OTIMIZAÇÃO] .engine não encontrado, usando .pt: {model_path.name}"
                )
                logger.info(
                    f"    └─ Compile com: uv run yolo export model={model_path} format=engine half=True"
                )
                return model_path
            
            logger.error(f"[OTIMIZAÇÃO] Modelo não encontrado: {model_path}")
            return model_path  # Retorna original (falhará no carregamento)
        
        # Outros formatos: retorna diretamente
        return model_path

    def infer(self, frame_bgra: np.ndarray) -> list[DetectionResult]:
        t0 = time.perf_counter()

        # Descarta o canal Alpha se vier em BGRA (MSS)
        if frame_bgra.shape[2] == 4:
            frame_input = frame_bgra[:, :, :3]
            # ✅ OTIMIZAÇÃO: Garante contiguidade para GPU/TensorRT
            # Slicing NumPy pode criar view não-contígua; força cópia contígua
            # apenas se necessário (evita overhead desnecessário)
            if not frame_input.flags['C_CONTIGUOUS']:
                frame_input = np.ascontiguousarray(frame_input)
        else:
            frame_input = frame_bgra

        t1 = time.perf_counter()

        # Execução de inferência
        if self.is_engine or self.model is None:
            # Inferência via TensorRT compilado (executa direto em C++/CUDA sem overhead de PyTorch)
            results = self.yolo.predict(
                source=frame_input,
                imgsz=self.imgsz,
                conf=self.conf_thresh,
                device=self.device,
                verbose=False,
            )
        else:
            # Fallback direto caso esteja com arquivo .pt
            results = self.yolo.predict(
                source=frame_input,
                imgsz=self.imgsz,
                conf=self.conf_thresh,
                device=self.device,
                verbose=False,
            )

        t2 = time.perf_counter()

        detections: list[DetectionResult] = []
        if results and len(results) > 0:
            boxes = results[0].boxes
            if boxes is not None and len(boxes) > 0:
                xywh = boxes.xywh.cpu().numpy()
                confs = boxes.conf.cpu().numpy()
                clss = boxes.cls.cpu().numpy()

                for i, (cx, cy, w, h) in enumerate(xywh):
                    cls_id = int(clss[i])
                    cls_name = self.names.get(cls_id, str(cls_id))

                    detections.append(
                        DetectionResult(
                            x=float(cx - w / 2.0),
                            y=float(cy - h / 2.0),
                            width=float(w),
                            height=float(h),
                            confidence=float(confs[i]),
                            class_name=cls_name,
                        )
                    )

        t3 = time.perf_counter()

        self.last_preprocess_ms = (t1 - t0) * 1000.0
        self.last_inference_ms = (t2 - t1) * 1000.0
        self.last_postprocess_ms = (t3 - t2) * 1000.0

        return detections
