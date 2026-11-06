"""Writes recognition.onnx and characters.txt next to this file from PaddleOCR's PP-OCRv6 small
text recognition model (Apache-2.0):

    uv run --with onnx==1.23.2 --with numpy==2.5.4 --with pyyaml==6.0.3 crates/ocr/model/convert.py

The weights of convolutions and matrix products are stored as int8 per output channel and turned
back into floats as the model runs, which keeps the model a quarter of its size and its results
those of the original; computing in int8 as well made most addresses wrong. They are turned back
by Cast and Mul: DequantizeLinear takes a channel axis only from opset 13, and the model is 11.
"""

import io
import pathlib
import urllib.request

import numpy as np
import onnx
import yaml
from onnx import helper, numpy_helper

SOURCE = "https://huggingface.co/PaddlePaddle/PP-OCRv6_small_rec_onnx/resolve/b8f84f0b80c529de40b4fbb3544b84fa7233a513"
HERE = pathlib.Path(__file__).parent


def fetch(name):
    with urllib.request.urlopen(f"{SOURCE}/{name}") as response:
        return response.read()


model = onnx.load_from_string(fetch("inference.onnx"))
# Every entry a string: a plain YAML load reads some characters as other values.
config = yaml.load(io.BytesIO(fetch("inference.yml")), Loader=yaml.BaseLoader)
(HERE / "characters.txt").write_text("\n".join(config["PostProcess"]["character_dict"]) + "\n")

graph = model.graph
users = {}
for node in graph.node:
    for position, name in enumerate(node.input):
        users.setdefault(name, set()).add((node.op_type, position))
dequantize = []
for initializer in list(graph.initializer):
    weights = numpy_helper.to_array(initializer)
    uses = users.get(initializer.name, set())
    if uses == {("Conv", 1)}:
        axis = 0
    elif uses == {("MatMul", 1)} and weights.ndim == 2:
        axis = 1
    else:
        continue
    name = initializer.name
    others = tuple(i for i in range(weights.ndim) if i != axis)
    scale = np.abs(weights).max(axis=others) / 127
    scale[scale == 0] = 1
    shape = [1] * weights.ndim
    shape[axis] = -1
    quantized = np.round(weights / scale.reshape(shape)).astype(np.int8)
    graph.initializer.remove(initializer)
    graph.initializer.extend(
        [
            numpy_helper.from_array(quantized, f"{name}_q"),
            numpy_helper.from_array(scale.reshape(shape).astype(np.float32), f"{name}_scale"),
        ]
    )
    dequantize += [
        helper.make_node("Cast", [f"{name}_q"], [f"{name}_float"], to=onnx.TensorProto.FLOAT),
        helper.make_node("Mul", [f"{name}_float", f"{name}_scale"], [name]),
    ]
nodes = dequantize + list(graph.node)
del graph.node[:]
graph.node.extend(nodes)
# The notice Apache-2.0 asks a changed file to carry.
model.doc_string = (
    f"PaddleOCR PP-OCRv6 small text recognition (Apache-2.0), from {SOURCE}/inference.onnx; "
    "changed for Auto Email Sender to store the weights of convolutions and matrix products as "
    "int8 per output channel."
)
onnx.checker.check_model(model, full_check=True)
onnx.save(model, HERE / "recognition.onnx")
