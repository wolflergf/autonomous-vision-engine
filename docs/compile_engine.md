# Compile Engine Documentation - Autonomous Vision Engine

## Build & Compilation Prerequisites

- CUDA Toolkit compatible with PyTorch installation
- TensorRT bindings for the specific CUDA version
- uv environment manager (already configured in pyproject.toml)
- NVIDIA GPU with compute capability matching the engine binary

## Engine Compilation Pipeline

1. Export PyTorch YOLO model to ONNX format
   ```
   uv run yolo export model=models/model.pt format=onnx imgsz=640 device=0
   ```
2. Convert ONNX to TensorRT FP16 engine
   ```
   uv run yolo export model=models/model.pt format=engine half=True imgsz=640 device=0
   ```
3. Optional INT8 quantization
   - Requires representative dataset and TensorRT INT8 support

## Runtime Validation & Benchmarks

- Latency benchmarks: measure frame processing time across 100+ frames
- Memory footprint: VRAM usage before/after engine loading
- Cold-start vs. warm-loop execution timing differences
- Precision validation: mAP@0.5 comparison between .pt and .engine artifacts

## Artifact Packaging & Deployment

- Engine binaries named {Stem}.engine (capitalized, e.g., ModelName.engine)
- Fallback to .pt model if .engine not present
- Platform-specific binaries (engine compiled for specific GPU architecture)
- Version metadata embedded in engine binary for compatibility checking

## Example Compilation Command

```
uv run yolo export model=models/model.pt format=engine half=True imgsz=640 device=0
```

## Deployment Checklist

- Verify engine binary exists: models/ModelName.engine
- Confirm GPU driver compatibility with TensorRT version
- Test warm-loop inference latency (< 5ms target)
- Validate precision drop (< 0.5% mAP degradation)
- Ensure CUDA version compatibility between engine and runtime