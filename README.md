# Aion 2 — Automation Engine

Motor de automação para **Aion 2** que captura a janela do jogo, detecta monstros
com um modelo **YOLO** acelerado por GPU e executa combate automaticamente (ataques,
skills, combos, loot e exploração) via o driver de kernel **Interception**.

A base de código segue uma **arquitetura limpa e desacoplada**: contratos em `core/`,
motor em `engine/`, interface em `ui/` e código legado isolado em `archive/`.

---

## Arquitetura Modular

```
Aion2_Automation/
├── main.py                 # Entry point (exige Administrador) → abre a GUI
├── core/                   # Contratos e config compartilhados
│   ├── interfaces.py       # ICaptureEngine, IInferenceEngine, IInputDriver, DetectionResult
│   ├── taxonomy.py         # Mapeamento único de classes do modelo (Target/Loot/Enemy…)
│   └── config.py           # AutomationConfig (carrega/salva JSON atômico)
├── engine/                 # Motor de automação
│   ├── capture_worker.py       # Captura da janela (mss, buffer BGRA)
│   ├── onnx_client.py          # Inferência YOLO (.pt / TensorRT .engine)
│   ├── state_machine.py        # CombatFSM: STANDBY→ROAMING→COMBAT→COOLDOWN
│   ├── target_selector.py      # Seleção de alvo mais próximo (por classe da taxonomia)
│   ├── roam_controller.py      # Exploração: andar (W) + giro de câmera 90°
│   ├── combat_pattern.py       # Cadência de combate (ataques/skills, modo pânico 3+)
│   └── interception_driver.py  # Implementação de IInputDriver sobre input_controller
├── ui/
│   └── main_windows.py     # GUI PySide6 + AutomationWorker (QThread)
├── skill_combo_manager.py  # Execução de skills/combos (injeção de IInputDriver)
├── input_controller.py     # API de input 100% via Interception (sem fallback)
├── combat_queue.py         # Prevenção de pressionamentos duplicados (usado pelo input)
├── utils.py                # Helpers de janela/imagem
├── skill_combo_config.json # Config de skills, combos e intervalos
└── archive/                # Módulos legados obsoletos (isolados, NÃO usados)
```

**Fluxo de execução:**

```
main.py
  └─ AutomationWorker (QThread)
       CaptureEngine ──frame──▶ OnnxYoloClient ──detections──▶ CombatFSM
                                                                 │  ├─ TargetSelector
                                                                 │  ├─ CombatPattern
                                                                 │  └─ RoamController
                                                                 ▼
                                            InterceptionDriver → input_controller (kernel)
```

---

## Requisitos de Sistema

| Item | Detalhe |
|---|---|
| **SO** | Windows 10/11 (64-bit) |
| **Permissão** | **Administrador** — obrigatório para o driver Interception (entrada em nível de kernel). O `main.py` e o `run_bot.bat` solicitam elevação automaticamente. |
| **Driver** | Interception instalado (DLL em `Interception/library/x64/interception.dll`). |
| **GPU** | Recomendada (CUDA 12.4). O índice PyTorch do projeto já aponta para `cu124`. Sem GPU, roda em CPU (mais lento). |
| **Python** | 3.11 – 3.12 (via `uv`) |

### Dependências
Gerenciadas pelo `uv` (veja `pyproject.toml` / `uv.lock`):
`torch`, `torchvision`, `ultralytics`, `pyside6`, `opencv-python`, `mss`, `loguru`,
`pywin32`, `pygetwindow`, `onnx`.

**Modelo:** `models/aion.pt` (ou `aion.onnx` / `aion__.engine`). As classes suportadas
estão em `core/taxonomy.py`.

---

## Inicialização

### Opção recomendada — `run_bot.bat`
Dê duplo clique (ou execute no terminal). O launcher:
1. **Solicita elevação de Administrador** automaticamente se necessário.
2. Exibe um menu para escolher o modo:

```
  [1] Modo Seguro   (DRY_RUN=1)  — simula entradas, NÃO envia teclas/mouse ao jogo
  [2] Modo Real     (Interception) — envia entradas reais em nível de kernel
```

3. Executa `uv run main.py` com a variável de ambiente correta.

> ⚠️ **Comece sempre pelo Modo Seguro [1]** para validar detecção, HUD e a FSM sem
> riscos. Só use o **Modo Real [2]** com o jogo em foco e após conferir o checklist.

### Opção manual (PowerShell como Administrador)
```powershell
# Modo Seguro
$env:INPUT_DRY_RUN = "1"; uv run main.py

# Modo Real
uv run main.py
```

---

## Checklist In-Game (antes do Modo Real)

- [ ] Jogo aberto em **modo janela** ou **sem borda** (a captura usa o retângulo da janela).
- [ ] Personagem posicionado na **área de caça/farm** com o alvo já travável.
- [ ] **Auto-target on skill use: ON** (Combate → Controles) — as skills miram no inimigo mais próximo.
- [ ] Teclas de ataque/skills configuradas conforme `skill_combo_config.json`
      (`SKILL_COOLDOWNS`, `COMBO_SETS`). Evite keybinds inválidos.
- [ ] Skills/combos nas barras corretas; cooldowns conferidos.
- [ ] Resolução compatível com a calibração do modelo (a FSM atualiza o `screen_size` a cada frame).

---

## Modos de Operação

| Modo | Comportamento |
|---|---|
| **HYBRID / ASSIST** | Combate reativo; **não** vagueia pelo mapa (roaming desligado). |
| **FULL_AUTO** | Combate + **roaming** automático (anda `W`, gira a câmera 90° procurando monstros). |

A FSM gerencia a concorrência do mouse: ao encontrar um alvo durante o roaming, ela
**aborta o movimento e aguarda o botão do mouse ser liberado** antes de atacar.

---

## Notas

- **Sem fallback de input:** o `input_controller` usa exclusivamente o Interception.
  Se a DLL/driver não carregar, as chamadas levantam `RuntimeError` explicativo.
- **Logs:** use `loguru`; o aviso `Unknown skill keybind` indica uma tecla de skill
  não mapeada em `SKILL_COOLDOWNS` — ajuste `skill_combo_config.json`.
- **Código legado:** tudo que ficou obsoleto está em `archive/` (referência histórica,
  não é importado pelo fluxo ativo).
