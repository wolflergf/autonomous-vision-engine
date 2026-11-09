#!/usr/bin/env python
"""Extrai coordenadas normalizadas das barras de HP/MP de uma screenshot estática.

Lê uma imagem (ex.: ``vitalit.png``), isola as barras por cor (vermelho = HP,
azul = MP), valida que o contorno tem geometria de "barra" (larga e fina) e
grava as coordenadas normalizadas em ``HEALING.HP_BAR`` / ``HEALING.MP_BAR``
do ``skill_combo_config.json`` (escrita atômica).

Uso:
    uv run python tools/extract_from_screenshot.py
    uv run python tools/extract_from_screenshot.py --image vitalit.png --debug vitals_debug.png --no-write
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import cv2
import numpy as np

CONFIG_PATH = Path("skill_combo_config.json")

# Bandas de busca normalizadas (x0,x1 / y0,y1) — HUD inferior central.
HP_SEARCH = {"x": (0.25, 0.70), "y": (0.85, 0.96)}
MP_SEARCH = {"x": (0.40, 0.80), "y": (0.85, 0.97)}

# Critérios de "barra": larga e fina.
MIN_ASPECT = 4.0
MAX_BAR_H = 40
MIN_FILL = 0.40
MIN_AREA = 800

# Faixa HSV do MP (ciano/turquesa claro da barra de mana).
MP_HUE = (80, 105)
MP_S_MIN = 80
MP_V_MIN = 100


def _atomic_write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _hue_mask(hsv: np.ndarray, lo: int, hi: int, s_min: int, v_min: int) -> np.ndarray:
    if lo <= hi:
        return cv2.inRange(hsv, (lo, s_min, v_min), (hi, 255, 255))
    return cv2.inRange(hsv, (lo, s_min, v_min), (180, 255, 255)) | cv2.inRange(
        hsv, (0, s_min, v_min), (hi, 255, 255)
    )


def _banded_mask(hsv, lo, hi, s_min, v_min, search):
    H, W = hsv.shape[:2]
    mask = _hue_mask(hsv, lo, hi, s_min, v_min)
    x0, x1 = int(search["x"][0] * W), int(search["x"][1] * W)
    y0, y1 = int(search["y"][0] * H), int(search["y"][1] * H)
    band = np.zeros_like(mask)
    band[y0:y1, x0:x1] = 255
    mask = cv2.bitwise_and(mask, band)
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (7, 3)))


def find_bar(hsv, lo, hi, s_min, v_min, search):
    """Melhor contorno com geometria de barra na banda, ou None."""
    mask = _banded_mask(hsv, lo, hi, s_min, v_min, search)
    H, W = hsv.shape[:2]
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        if w * h < MIN_AREA:
            continue
        aspect = w / max(h, 1)
        fill = cv2.contourArea(c) / (w * h)
        if aspect < MIN_ASPECT or h > MAX_BAR_H or fill < MIN_FILL:
            continue
        score = w * fill
        if best is None or score > best["score"]:
            best = {
                "norm": (round(x / W, 4), round(y / H, 4), round((x + w) / W, 4), round((y + h) / H, 4)),
                "px": (x, y, w, h), "score": score, "aspect": aspect, "fill": fill,
            }
    return best


def list_candidates(hsv, lo, hi, s_min, v_min, search, topn=4):
    """Maiores candidatos (sem filtro de barra) para inspeção/debug."""
    mask = _banded_mask(hsv, lo, hi, s_min, v_min, search)
    H, W = hsv.shape[:2]
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        if w * h < 400:
            continue
        out.append({"px": (x, y, w, h), "area": w * h})
    out.sort(key=lambda r: -r["area"])
    return out[:topn]


def _mirror_roi(hp_norm: tuple[float, float, float, float], pad: float = 0.004) -> dict:
    """ROI de busca do MP: espelha a barra de HP em torno do centro (x=0.5).

    O HUD é simétrico; a barra de mana fica à direita do escudo central,
    na mesma faixa vertical da barra de HP. Restringe a busca para não
    confundir a barra com ícones cianos da skillbar (que ficam em y~0.95).
    """
    hx0, hy0, hx1, hy1 = hp_norm
    mx0 = max(0.0, 1.0 - hx1 - pad)
    mx1 = min(1.0, 1.0 - hx0 + pad)
    my0 = max(0.0, hy0 - pad)
    my1 = min(1.0, hy1 + pad)
    return {"x": (round(mx0, 4), round(mx1, 4)), "y": (round(my0, 4), round(my1, 4))}


def main() -> int:
    ap = argparse.ArgumentParser(description="Extrai coordenadas HP/MP de uma screenshot.")
    ap.add_argument("--image", default="vitalit.png", help="Imagem de origem (padrão: vitalit.png)")
    ap.add_argument("--debug", default="vitals_debug.png", help="Imagem de debug com retângulos")
    ap.add_argument("--no-write", action="store_true", help="Não grava no JSON (só detecta/preview)")
    args = ap.parse_args()

    if not os.path.exists(args.image):
        print(f"[ERRO] Imagem não encontrada: {args.image}")
        return 1

    img = cv2.imread(args.image)
    if img is None:
        print(f"[ERRO] Falha ao decodificar a imagem: {args.image}")
        return 1
    H, W = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    print(f"[INFO] Imagem {args.image} carregada: {W}x{H}")

    # ---- HP (vermelho, cobre hue wrap em 0/180) ----
    hp = find_bar(hsv, 0, 10, 90, 60, HP_SEARCH) or find_bar(hsv, 170, 180, 90, 60, HP_SEARCH)
    # ---- MP (ciano/turquesa) — busca no ROI espelhado do HP quando disponível ----
    mp_roi = _mirror_roi(hp["norm"]) if hp else MP_SEARCH
    mp = find_bar(hsv, MP_HUE[0], MP_HUE[1], MP_S_MIN, MP_V_MIN, mp_roi)

    debug = img.copy()
    results = {}

    if hp:
        x, y, w, h = hp["px"]
        nx0, ny0, nx1, ny1 = hp["norm"]
        cv2.rectangle(debug, (x, y), (x + w, y + h), (0, 255, 0), 2)
        results["HP_BAR"] = {"x0": nx0, "y0": ny0, "x1": nx1, "y1": ny1}
        print(f"[HP] barra detectada  px=({x},{y},{w}x{h})  "
              f"norm=({nx0},{ny0},{nx1},{ny1})  aspect={hp['aspect']:.1f} fill={hp['fill']:.2f}")
    else:
        print("[HP] NENHUMA barra com geometria válida detectada. Candidatos brutos:")
        for c in list_candidates(hsv, 170, 180, 90, 60, HP_SEARCH):
            print(f"       px={c['px']} area={c['area']}")

    if mp:
        x, y, w, h = mp["px"]
        nx0, ny0, nx1, ny1 = mp["norm"]
        cv2.rectangle(debug, (x, y), (x + w, y + h), (255, 255, 0), 2)
        results["MP_BAR"] = {"x0": nx0, "y0": ny0, "x1": nx1, "y1": ny1}
        print(f"[MP] barra detectada  px=({x},{y},{w}x{h})  "
              f"norm=({nx0},{ny0},{nx1},{ny1})  aspect={mp['aspect']:.1f} fill={mp['fill']:.2f}")
    else:
        print("[MP] NENHUMA barra azul válida detectada (só ícones). Candidatos brutos:")
        for c in list_candidates(hsv, 95, 135, 70, 60, MP_SEARCH):
            print(f"       px={c['px']} area={c['area']}")

    cv2.imwrite(args.debug, debug)
    print(f"[INFO] Debug salvo em: {args.debug}")

    if not results:
        print("[AVISO] Nada a gravar — nenhuma barra válida detectada. JSON intacto.")
        return 0

    if args.no_write:
        print("[INFO] --no-write ativo: JSON NÃO modificado.")
        return 0

    # Gravação atômica preservando lum_threshold padrão.
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    healing = data.setdefault("HEALING", {})
    for key, box in results.items():
        cur = healing.get(key, {})
        healing[key] = {
            "x0": box["x0"], "y0": box["y0"], "x1": box["x1"], "y1": box["y1"],
            "lum_threshold": cur.get("lum_threshold", 55),
        }
    _atomic_write_json(CONFIG_PATH, data)
    print(f"[OK] skill_combo_config.json atualizado: {', '.join(results.keys())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

