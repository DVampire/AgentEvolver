"""Every serializer must handle every message type that can reach it.

`derive_context` produces `ToolMessage`, and not one of the six serializers knew it —
each raised `Unknown message type`. The path that produces the type is off by default, so
the gap sat there until the switch was turned on, and then it failed on every step after
the first while the run reported success.

Discovery, not a list: the subjects are every `Message` subclass and every provider
package found at import time. Add a provider or a message type without covering it and
this fails, which a hand-maintained list cannot do — the previous list was the six
serializers themselves, and they agreed with each other.
"""

import importlib
import inspect
import pkgutil
from pathlib import Path

import pytest

import agentevolver.message.types as message_types
import agentevolver.model as model_package
from agentevolver.message.types import (AssistantMessage, Function, HumanMessage,
                                        Message, SystemMessage, ToolCall, ToolMessage)

# One instance per subclass. Constructed here rather than generated, because a message's
# required fields carry meaning a factory would have to invent — `tool_call_id` is the
# hinge every provider pairs on, and a blank one would pass a gate the wire rejects.
SAMPLES = {
    SystemMessage: SystemMessage(content="rules"),
    HumanMessage: HumanMessage(content="do the thing"),
    AssistantMessage: AssistantMessage(content="on it", tool_calls=[ToolCall(
        id="call_1", function=Function(name="write_file_tool", arguments='{"path": "a"}'))]),
    ToolMessage: ToolMessage(content="wrote a", tool_call_id="call_1",
                             name="write_file_tool"),
}


def _message_subclasses():
    return {v for v in vars(message_types).values()
            if inspect.isclass(v) and issubclass(v, Message) and v is not Message}


def _serializers():
    """Every `*Serializer` class in every provider package, with its entry point.

    Providers name the entry point differently (`serialize` vs `serialize_message`), so
    the gate finds it rather than assuming — an assumption here would silently skip a
    provider and report green.
    """
    found = []
    root = Path(model_package.__file__).parent
    for module_info in pkgutil.iter_modules([str(root)]):
        if not module_info.ispkg:
            continue
        try:
            module = importlib.import_module(
                f"agentevolver.model.{module_info.name}.serializer")
        except ModuleNotFoundError:
            continue
        for name, obj in vars(module).items():
            if not (inspect.isclass(obj) and name.endswith("Serializer")):
                continue
            if obj.__module__ != module.__name__:
                continue          # re-exported from elsewhere; covered at its own module
            entry = next((f for f in ("serialize_message", "serialize")
                          if callable(getattr(obj, f, None))), None)
            if entry:
                found.append((f"{module_info.name}.{name}", obj, entry))
    return found


SERIALIZERS = _serializers()


def test_something_was_actually_found():
    """A test that discovers nothing passes vacuously and checks nothing."""
    assert len(SERIALIZERS) >= 5, f"expected every provider; found {SERIALIZERS}"
    assert _message_subclasses(), "no Message subclasses found"


def test_every_subclass_has_a_sample():
    """Adding a message type must fail here, not at runtime on one provider.

    This is the half a per-serializer test cannot cover: the six serializers were each
    self-consistent and all wrong together.
    """
    missing = _message_subclasses() - set(SAMPLES)
    assert not missing, (
        f"no sample for {sorted(c.__name__ for c in missing)} — add one to SAMPLES so "
        f"every serializer is checked against it")


@pytest.mark.parametrize("label,serializer,entry", SERIALIZERS,
                         ids=[label for label, _, _ in SERIALIZERS])
def test_a_serializer_handles_every_message_type(label, serializer, entry):
    serialize = getattr(serializer, entry)
    failures = []
    for cls, sample in SAMPLES.items():
        try:
            result = serialize(sample)
        except Exception as error:                                  # noqa: BLE001
            failures.append(f"{cls.__name__}: {type(error).__name__}: {error}")
            continue
        if not result:
            failures.append(f"{cls.__name__}: serialized to {result!r}")
    assert not failures, f"{label} cannot serialize:\n  " + "\n  ".join(failures)


@pytest.mark.parametrize("label,serializer,entry", SERIALIZERS,
                         ids=[label for label, _, _ in SERIALIZERS])
def test_a_tool_result_keeps_whatever_pairs_it(label, serializer, entry):
    """Serializing without error is not enough; the pairing has to survive.

    A result the provider cannot match to its call is rejected outright — "each
    tool_result block must have a corresponding tool_use block" — so a serializer can
    pass the test above and still produce an unusable history.

    What does the pairing is provider-specific, and stating it as "the id must appear"
    is wrong: Gemini matches a `function_response` by declared function *name* and never
    sees the id. This caught that on its first run, against its own first draft. So
    the invariant is the weaker true one — one of the two identifiers survives — and each
    provider's exact shape is pinned in `test_tool_message_roundtrip.py`.
    """
    sample = SAMPLES[ToolMessage]
    result = repr(getattr(serializer, entry)(sample))
    assert sample.tool_call_id in result or (sample.name and sample.name in result), (
        f"{label} kept neither the call id nor the tool name; nothing pairs the result "
        f"to the call that produced it")
