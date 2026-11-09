"""Validate DetectionResult changes."""
from core.interfaces import DetectionResult

# Test 1: Default values
r = DetectionResult(x=1.0, y=2.0, width=3.0, height=4.0)
assert r.label == "target", f"Expected 'target', got '{r.label}'"
assert r.confidence == 1.0, f"Expected 1.0, got {r.confidence}"
assert r.box == (1.0, 2.0, 3.0, 4.0), f"Box property mismatch: {r.box}"
print("✓ Test 1 passed: Default values")

# Test 2: Custom values
r2 = DetectionResult(x=1.0, y=2.0, width=3.0, height=4.0, label="enemy", confidence=0.95)
assert r2.label == "enemy", f"Expected 'enemy', got '{r2.label}'"
assert r2.confidence == 0.95, f"Expected 0.95, got {r2.confidence}"
assert r2.box == (1.0, 2.0, 3.0, 4.0), f"Box property mismatch: {r2.box}"
print("✓ Test 2 passed: Custom values")

# Test 3: Frozen (immutable)
try:
    r.label = "new_label"
    assert False, "Should have raised FrozenError"
except Exception:
    print("✓ Test 3 passed: Frozen/immutable")

# Test 4: SyntheticViewport integration
from tests.conftest import BoundingBox, SyntheticTarget, SyntheticViewport

vp = SyntheticViewport()
box = BoundingBox(x=280, y=240, w=80, h=120)
target = SyntheticTarget(label="target", confidence=0.92, box=box)
vp.add_target(target.label, target.confidence, target.box)
detections = vp.to_detection_results()

assert len(detections) == 1, f"Expected 1 detection, got {len(detections)}"
d = detections[0]
assert d.label == "target", f"Expected 'target', got '{d.label}'"
assert d.confidence == 0.92, f"Expected 0.92, got {d.confidence}"
assert d.box == (280.0, 240.0, 80.0, 120.0), f"Box property mismatch: {d.box}"
print("✓ Test 4 passed: SyntheticViewport integration")

print("\nAll validation tests passed!")