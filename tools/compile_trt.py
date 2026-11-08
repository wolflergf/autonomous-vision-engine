import sys
from pathlib import Path

import onnx
from onnx import numpy_helper
import numpy as np
import tensorrt as trt

logger = trt.Logger(trt.Logger.INFO)


def convert_onnx_to_fp16(onnx_path: Path) -> bytes:
    """Converte um modelo ONNX de FP32 para FP16.
    
    Converte:
    - Todos os initializers (pesos) de float32 para float16
    - Todos os tipos de tensor de input/output de FLOAT para HALF
    
    Isso e necessario porque o TensorRT 11.4 removeu BuilderFlag.FP16.
    Em redes strongly typed (TRT 11+), o precision e controlado pelos tipos
    de tensores do modelo, nao por flags do builder.
    """
    print(f"[TRT] Convertendo modelo ONNX para FP16 (antes da compilacao TensorRT)...")
    model = onnx.load(str(onnx_path))

    # Converte initializers (pesos) de FP32 para FP16
    converted_initializers = 0
    for initializer in model.graph.initializer:
        if initializer.data_type == onnx.TensorProto.FLOAT:
            arr = numpy_helper.to_array(initializer)
            arr_fp16 = arr.astype(np.float16)
            new_init = numpy_helper.from_array(arr_fp16, initializer.name)
            initializer.CopyFrom(new_init)
            converted_initializers += 1

    # Converte tipos de input/output para HALF
    for input_tensor in model.graph.input:
        if input_tensor.type.tensor_type.elem_type == onnx.TensorProto.FLOAT:
            input_tensor.type.tensor_type.elem_type = onnx.TensorProto.FLOAT16

    for output_tensor in model.graph.output:
        if output_tensor.type.tensor_type.elem_type == onnx.TensorProto.FLOAT:
            output_tensor.type.tensor_type.elem_type = onnx.TensorProto.FLOAT16

    # Tambem converte tipos em value_info (tensores intermediarios)
    for vi in model.graph.value_info:
        if vi.type.tensor_type.elem_type == onnx.TensorProto.FLOAT:
            vi.type.tensor_type.elem_type = onnx.TensorProto.FLOAT16

    onnx.checker.check_model(model)
    model_bytes = model.SerializeToString()

    print(
        f"[TRT] Conversao concluida: {converted_initializers} initializers convertidos para FP16"
    )
    return model_bytes


def build_engine(onnx_path: Path, engine_path: Path, workspace_gb: int = 2) -> bool:
    if not onnx_path.exists():
        print(f"Erro: Arquivo ONNX nao encontrado em '{onnx_path}'")
        return False

    builder = trt.Builder(logger)

    # TensorRT 10/11+: create_network() sem argumentos (EXPLICIT_BATCH e o padrao)
    network = builder.create_network()
    parser = trt.OnnxParser(network, logger)

    # Converte o modelo ONNX para FP16 antes de fazer o parse
    model_bytes = convert_onnx_to_fp16(onnx_path)

    print(f"[TRT] Fazendo parse do modelo ONNX (FP16)...")
    if not parser.parse(model_bytes):
        print("[TRT] Falha ao analisar o modelo ONNX:")
        for i in range(parser.num_errors):
            print(f"  - {parser.get_error(i)}")
        return False

    config = builder.create_builder_config()
    config.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, workspace_gb * (1024**3))

    print("[TRT] Otimizando kernels e compilando engine (leva de 2 a 5 minutos)...")
    serialized_engine = builder.build_serialized_network(network, config)

    if serialized_engine is None:
        print("[TRT] Erro: Falha na construcao do serialized network.")
        return False

    engine_path.parent.mkdir(parents=True, exist_ok=True)
    with open(engine_path, "wb") as f:
        f.write(bytes(serialized_engine))

    print(f"[TRT] Sucesso! Engine salvo em: {engine_path}")
    return True


if __name__ == "__main__":
    onnx_file = Path("models/aion.onnx")
    engine_file = Path("models/Aion.engine")

    success = build_engine(onnx_file, engine_file)
    sys.exit(0 if success else 1)
