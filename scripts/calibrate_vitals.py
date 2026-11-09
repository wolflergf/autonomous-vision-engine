"""Utilitário de calibração da barra de HP (e MP) para o VitalsMonitor.

Fluxo:
    1. Captura um frame atual da janela do jogo via `mss` (mesma fonte do
       CaptureEngine), opcionalmente filtrando por substring do título.
    2. Abre uma janela OpenCV e usa `cv2.selectROI` para arrastar/selecionar
       visualmente o retângulo da barra de vida.
    3. Converte os pixels selecionados em coordenadas normalizadas (0.0-1.0)
       dividindo pela largura/altura do frame.
    4. Mostra uma prévia da máscara de luminância (BT.601) da região com um
       trackbar de limiar para validar/ajustar `lum_threshold` ao vivo.
    5. Salva `HEALING.HP_BAR` (x0, y0, x1, y1, lum_threshold) em
       `skill_combo_config.json` de forma atômica.

Requer GUI (não roda headless). Execute o jogo em modo janela antes de rodar.

Uso:
    uv run python tools/calibrate_vitals.py
    uv run python tools/calibrate_vitals.py --window "Aion2"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

import cv2
import mss
import numpy as np

# Permite rodar como script solto (`python tools/calibrate_vitals.py`).
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CONFIG_PATH = ROOT / "skill_combo_config.json"

# Modos suportados: nome -> (chave JSON em HEALING, rótulo exibido na UI).
MODES = {
    "hp": ("HP_BAR", "HP"),
    "mp": ("MP_BAR", "MP"),
    "target": ("TARGET_BAR", "TARGET"),
}

# Luminância BT.601 (mesma heurística do VitalsMonitor.read_hp).
_W_B, _W_G, _W_R = 0.114, 0.587, 0.299


def _luminance(bgr: np.ndarray) -> np.ndarray:
    """Luminância BT.601 de um patch BGR/BGRA (ignora Alpha)."""
    crop = bgr[..., :3].astype(np.float32)
    return crop[..., 0] * _W_B + crop[..., 1] * _W_G + crop[..., 2] * _W_R


def _list_windows() -> list[tuple[str, int]]:
    """Janelas visíveis (título, hwnd)."""
    try:
        from utils import list_windows

        return list_windows()
    except Exception:
        return []


def _resolve_rect(substr: str | None) -> tuple[int, int, int, int]:
    """Resolve o retângulo (left, top, w, h) a capturar.

    Sem `substr`: captura o monitor primário. Com `substr`: usa a primeira
    janela cujo título contenha a substring (ignora Visual Studio Code).
    """
    if substr:
        for title, hwnd in _list_windows():
            # Ignora Visual Studio Code (evita selecionar o próprio editor).
            if "visual studio code" in title.lower():
                continue
            if substr.lower() in title.lower():
                from utils import get_window_rect

                rect = get_window_rect(hwnd)
                if rect:
                    print(f"[calib] janela selecionada: {title!r} rect={rect}")
                    return rect
        print(f"[calib] AVISO: nenhuma janela com {substr!r}; usando monitor primário.")
    with mss.MSS() as sct:  # mss.mss() deprecated → mss.MSS()
        mon = sct.monitors[1]  # 0 = mosaico de todos; 1 = primário
    return mon["left"], mon["top"], mon["width"], mon["height"]


def _grab_frame(left: int, top: int, w: int, h: int) -> np.ndarray:
    """Captura um frame BGRA cru (igual ao CaptureEngine)."""
    with mss.MSS() as sct:  # mss.mss() deprecated → mss.MSS()
        shot = sct.grab({"left": left, "top": top, "width": w, "height": h})
    return np.frombuffer(shot.raw, dtype=np.uint8).reshape((h, w, 4))


def _save_config(
    key: str, x0: float, y0: float, x1: float, y1: float, lum: int
) -> None:
    """Atualiza HEALING.<key> (ex.: HP_BAR) no JSON com escrita atômica."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    healing = data.setdefault("HEALING", {})
    healing[key] = {
        "x0": round(x0, 4),
        "y0": round(y0, 4),
        "x1": round(x1, 4),
        "y1": round(y1, 4),
        "lum_threshold": int(lum),
    }
    # Escrita atômica: grava em temp e substitui.
    fd, tmp = tempfile.mkstemp(dir=str(CONFIG_PATH.parent), suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, CONFIG_PATH)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _preview_and_tune(
    frame_bgr: np.ndarray,
    roi: tuple[int, int, int, int],
    init_threshold: int,
    label: str = "HP",
) -> tuple[int, bool]:
    """Mostra a máscara de luminância da ROI com trackbar de limiar.

    Retorna ``(threshold_escolhido, confirmado)``. O usuário ajusta o limiar
    vendo ao vivo quantas colunas ficam "preenchidas" (razão = barra simulada) e
    pressiona:
        ``s`` -> confirmar e seguir para salvar;
        ``r`` -> redesenhar/selecionar outra ROI;
        ``q`` -> cancelar sem salvar.
    """
    x, y, w, h = roi
    bgr = frame_bgr.copy()
    # Retângulo verde sobre a seleção (para conferência).
    cv2.rectangle(bgr, (x, y), (x + w, y + h), (0, 255, 0), 2)

    win = f"Calibracao {label} - [s]=salvar [r]=reselecionar [q]=sair"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.createTrackbar("lum_threshold", win, int(init_threshold), 255, lambda v: None)

    confirmed = False
    chosen = int(init_threshold)
    reselect = False
    while True:
        threshold = cv2.getTrackbarPos("lum_threshold", win)
        crop = _luminance(frame_bgr[y : y + h, x : x + w])
        col_mean = crop.mean(axis=0)  # Shape: (W,) - 1D array
        mask = (col_mean > threshold).astype(np.uint8) * 255
        filled_ratio = float((col_mean > threshold).sum()) / max(1, col_mean.size)

        # Visualiza a máscara como faixa horizontal legível (espelha em altura).
        # mask shape: (W,) → expand to (1, W) → repeat to (80, W) → stack to (80, W, 3) BGR
        bar = np.repeat(mask[None, :], max(1, 80), axis=0)  # Shape: (80, W)
        bar_bgr = np.stack([bar, bar, bar], axis=-1)  # Shape: (80, W, 3) BGR
        cv2.putText(
            bar_bgr,
            f"{label}~{filled_ratio * 100:5.1f}%  thr={threshold}",
            (8, 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (200, 200, 200),
            1,
            cv2.LINE_AA,
        )
        cv2.imshow(win, bar_bgr)

        key = cv2.waitKey(30) & 0xFF
        if key == ord("s"):
            confirmed, chosen = True, threshold
            break
        if key == ord("r"):
            reselect = True
            break
        if key == ord("q"):
            break
    cv2.destroyAllWindows()
    return chosen, confirmed, reselect


def _calibrate_single_bar(
    frame_bgr: np.ndarray, w: int, h: int, mode_key: str, label: str, default_thr: int
) -> bool:
    """Calibra uma única barra (HP/MP/TARGET).
    
    Retorna True se salvou com sucesso, False se cancelado.
    """
    # Limiar atualmente configurado como ponto de partida.
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cur = json.load(f).get("HEALING", {}).get(mode_key, {})
        start_thr = int(cur.get("lum_threshold", default_thr))
    except Exception:
        start_thr = default_thr

    while True:
        print(
            f"[calib] arraste sobre a barra de {label} e pressione "
            "ENTER/ESPACO (c=cancela)."
        )
        roi = cv2.selectROI(
            f"Selecione a barra de {label}",
            frame_bgr,
            showCrosshair=True,
            fromCenter=False,
        )
        cv2.destroyAllWindows()
        rx, ry, rw, rh = [int(v) for v in roi]
        if rw < 2 or rh < 2:
            print("[calib] selecao vazia; pulando esta barra.")
            return False

        thr, confirmed, reselect = _preview_and_tune(
            frame_bgr, (rx, ry, rw, rh), start_thr, label
        )
        if reselect:
            continue
        if not confirmed:
            print("[calib] cancelado pelo usuario; pulando esta barra.")
            return False

        x0, y0 = rx / w, ry / h
        x1, y1 = (rx + rw) / w, (ry + rh) / h
        _save_config(mode_key, x0, y0, x1, y1, thr)
        print(f"[calib] SALVO em HEALING.{mode_key}:")
        print(
            f"         x0={x0:.4f} y0={y0:.4f} x1={x1:.4f} y1={y1:.4f} "
            f"lum_threshold={thr}"
        )
        return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Calibra barras HP e MP do VitalsMonitor em sequência."
    )
    parser.add_argument(
        "--window",
        default="Aion2",
        help="Substring do título da janela do jogo (default: Aion2).",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=55,
        help="Limiar inicial de luminância (default: 55).",
    )
    parser.add_argument(
        "--mode",
        choices=sorted(MODES) + ["auto"],
        default="auto",
        help="Barra a calibrar: hp, mp, target ou auto (HP+MP em sequência, default).",
    )
    args = parser.parse_args()

    # Captura frame uma única vez (reutilizado para todas as barras).
    left, top, w, h = _resolve_rect(args.window)
    print(f"[calib] capturando frame {w}x{h} em ({left},{top})...")
    frame = _grab_frame(left, top, w, h)
    display = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

    # Modo automático: calibra HP e MP em sequência.
    if args.mode == "auto":
        print("\n" + "=" * 60)
        print("[calib] MODO AUTOMÁTICO: calibrando HP e MP em sequência")
        print("=" * 60 + "\n")
        
        # 1. Calibra HP
        print("[calib] --- ETAPA 1/2: Barra de HP ---")
        hp_key, hp_label = MODES["hp"]
        hp_saved = _calibrate_single_bar(display, w, h, hp_key, hp_label, args.threshold)
        
        if not hp_saved:
            print("[calib] HP não foi calibrada; encerrando.")
            return 1
        
        print("\n[calib] ✓ HP calibrada com sucesso!\n")
        
        # 2. Calibra MP
        print("[calib] --- ETAPA 2/2: Barra de MP ---")
        mp_key, mp_label = MODES["mp"]
        mp_thr = 70  # MP tem threshold mais alto (cor ciano/turquesa clara)
        mp_saved = _calibrate_single_bar(display, w, h, mp_key, mp_label, mp_thr)
        
        if not mp_saved:
            print("[calib] MP não foi calibrada; HP foi salva.")
            return 1
        
        print("\n[calib] ✓ MP calibrada com sucesso!")
        print("\n" + "=" * 60)
        print("[calib] CONCLUÍDO: HP e MP calibradas e salvas!")
        print("=" * 60)
        return 0
    
    # Modo manual: calibra apenas a barra especificada.
    key, label = MODES[args.mode]
    saved = _calibrate_single_bar(display, w, h, key, label, args.threshold)
    return 0 if saved else 1


if __name__ == "__main__":
    raise SystemExit(main())
