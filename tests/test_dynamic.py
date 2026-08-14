"""Turning generated source into a live object a provider will accept a call for.

This is the machinery a self-evolving system runs on. An LLM writes a tool class as a
string, and this module has to make it importable, read a parameter schema off its
signature, and hand the model a function-calling description. Every step is a place where
the generated code is stranger than hand-written code: an unparseable annotation, a class
that does not extend what it claims to, a symbol the code uses but never imports.

Two failures get their own attention because they are silent. An ``array`` schema without
``items`` is rejected by strict providers — Gemini among them — so the tool loads,
registers, appears in the listing, and fails only at the moment the model tries to call
it. And ``x-python-type`` is internal bookkeeping: leaking it into the payload sent to a
provider turns every request carrying that tool into an error about an unknown field.

The rest is degradation. Generated code that cannot be understood must produce a usable
fallback rather than an exception, because an exception here stops the evolution loop
that produced it.
"""

from typing import Any, Dict, List, Optional

import pytest
from pydantic import BaseModel, Field

from agentevolver.dynamic.server import (
    PYTHON_TYPE_FIELD,
    DynamicModuleManager,
)


@pytest.fixture
def dynamic():
    """A manager that unloads whatever it loaded.

    Loaded modules go into ``sys.modules``, so without the teardown one test's generated
    module stays visible to the whole interpreter for the rest of the session.
    """
    manager = DynamicModuleManager()
    yield manager
    for name in manager.list_loaded_modules():
        manager.unload_module(name)


# --------------------------------------------------------------------------- #
# Annotations to schema types
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "annotation, json_type, python_type",
    [
        (str, "string", "str"),
        (int, "integer", "int"),
        (float, "number", "float"),
        (bool, "boolean", "bool"),
        (dict, "object", "dict"),
        (list, "array", "list"),
        (List[str], "array", "list"),
        (Dict[str, Any], "object", "dict"),
        (Optional[str], "string", "Optional[str]"),
        (Optional[int], "integer", "Optional[int]"),
    ],
)
def test_annotations_map_to_json_and_python_types(dynamic, annotation, json_type, python_type):
    """Both halves matter: the JSON type goes to the provider, the Python string is kept
    under ``x-python-type`` and is what rebuilds the validating model later.

    ``Optional[X]`` is the case worth checking — it must reduce to ``X``'s JSON type,
    since JSON Schema has no way to say "or absent" in a plain type field.
    """
    assert dynamic.annotation_to_types(annotation) == (json_type, python_type)


def test_an_unannotated_parameter_is_treated_as_a_string(dynamic):
    """Generated code often omits annotations entirely. Refusing them would make the tool
    unloadable; ``string`` is the one type any argument can be delivered as."""
    import inspect

    assert dynamic.annotation_to_types(inspect._empty) == ("string", "Any")
    assert dynamic.annotation_to_types(None) == ("string", "Any")


@pytest.mark.parametrize(
    "annotation, expected",
    [
        (List[str], {"type": "string"}),
        (List[int], {"type": "integer"}),
        (Optional[List[str]], {"type": "string"}),
        (List[List[int]], {"type": "array", "items": {"type": "integer"}}),
        (list, {"type": "string"}),   # unparametrized falls back rather than omitting items
    ],
)
def test_an_array_always_declares_its_element_type(dynamic, annotation, expected):
    """Strict consumers (the Gemini API) reject an array schema without ``items``.

    The bare ``list`` row is the important one: with no element type to read, the
    tempting move is to omit ``items`` altogether, and that produces a tool that loads
    cleanly and then breaks every request the model tries to make with it.
    """
    assert dynamic.annotation_to_item_schema(annotation) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("str", str), ("string", str),
        ("int", int), ("integer", int),
        ("float", float), ("number", float),
        ("bool", bool), ("boolean", bool),
        ("dict", dict), ("object", dict),
        ("list", list), ("array", list),
        ("typing.str", str),
        ("  str  ", str),
    ],
)
def test_type_strings_parse_from_both_python_and_json_names(dynamic, text, expected):
    """The same parser reads type strings written by a model and type strings this module
    itself serialised, so both vocabularies arrive at it — ``str`` and ``string`` have to
    mean the same thing, as do a ``typing.`` prefix and stray whitespace."""
    assert dynamic.parse_type_string(text) is expected


def test_parametrized_type_strings_round_trip(dynamic):
    """These strings come back off disk when a persisted config is reloaded. Losing the
    parameter — reading ``List[int]`` as a bare ``list`` — quietly drops validation that
    the tool was saved with."""
    assert dynamic.parse_type_string("Optional[str]") == Optional[str]
    assert dynamic.parse_type_string("List[int]") == List[int]
    assert dynamic.parse_type_string("Dict[str, int]") == Dict[str, int]


def test_an_unparseable_type_string_degrades_to_any(dynamic):
    """Generated code carries anything; an unknown type must not stop the load.

    A tool annotated with a class this module has never heard of is still a usable tool
    — ``Any`` accepts the argument and the call goes through, where raising would lose
    the whole tool over one annotation.
    """
    assert dynamic.parse_type_string("SomeUserClass") is Any
    assert dynamic.parse_type_string("Dict[nonsense]") is dict


# --------------------------------------------------------------------------- #
# Docstrings as the source of parameter descriptions
# --------------------------------------------------------------------------- #
def test_google_style_argument_descriptions_are_extracted(dynamic):
    """These descriptions are the only thing telling the model what an argument means.

    The typed form ``timeout (int):`` has to parse as well as the bare one; a parser that
    only handles ``name:`` drops exactly the parameters somebody bothered to annotate.
    """
    descriptions = dynamic.parse_docstring_descriptions("""
        Run a shell command.

        Args:
            command: The command to run
            timeout (int): Seconds before giving up

        Returns:
            The output
    """)
    assert descriptions == {
        "command": "The command to run",
        "timeout": "Seconds before giving up",
    }


def test_parsing_stops_at_the_section_after_args(dynamic):
    """``Returns:`` content must not be mistaken for another parameter.

    Every line in a Returns block looks like ``name: description`` too, so a parser that
    runs to the end of the docstring invents arguments the function does not have — and
    the model then tries to pass them.
    """
    descriptions = dynamic.parse_docstring_descriptions("""
        Args:
            a: first

        Returns:
            value: not a parameter
    """)
    assert descriptions == {"a": "first"}


@pytest.mark.parametrize("docstring", ["", None, "Just a summary with no Args section."])
def test_a_docstring_without_arguments_yields_nothing(dynamic, docstring):
    """``None`` is the common one — an undocumented generated function has no docstring
    at all, and this runs before any schema can be built."""
    assert dynamic.parse_docstring_descriptions(docstring) == {}


# --------------------------------------------------------------------------- #
# Signatures to parameter schemas
# --------------------------------------------------------------------------- #
def test_a_signature_becomes_a_parameter_schema(dynamic):
    """Required-ness is read from the absence of a default, not from anything declared.

    ``additionalProperties: False`` is asserted because strict function-calling modes
    require it; without it the same tool is accepted by one provider and refused by
    another for reasons that never mention the schema.
    """
    def search(query: str, limit: int = 10):
        """Search things.

        Args:
            query: What to look for
            limit: How many results
        """

    schema = dynamic.get_parameters(search)
    assert schema["required"] == ["query"]
    assert schema["properties"]["query"]["description"] == "What to look for"
    assert schema["properties"]["limit"]["default"] == 10
    assert schema["additionalProperties"] is False


def test_a_list_parameter_carries_its_items_into_the_schema(dynamic):
    """The element type is worked out correctly in isolation elsewhere; this checks it
    actually reaches the emitted schema, which is the part a provider sees."""
    def tag(names: List[str]):
        """Tag things.

        Args:
            names: The tags
        """

    assert dynamic.get_parameters(tag)["properties"]["names"]["items"] == {"type": "string"}


def test_varargs_are_left_out_of_the_schema(dynamic):
    """``*args``/``**kwargs`` are not things the model can be asked to fill in.

    Emitted as properties they become arguments named ``args`` and ``kwargs`` that the
    model dutifully supplies and the call then rejects.
    """
    def flexible(a: str, *args, **kwargs):
        pass

    assert list(dynamic.get_parameters(flexible)["properties"]) == ["a"]


def test_a_parameterless_callable_gets_the_empty_schema(dynamic):
    """Providers still require a schema object for a no-argument tool, so this cannot
    come back as ``None`` or an empty dict."""
    def nothing():
        pass

    assert dynamic.get_parameters(nothing) == dynamic.default_parameters_schema()


def test_a_class_is_described_by_its_call_signature(dynamic):
    """Tools in this repo are classes, so the schema comes off ``__call__``.

    ``self`` is the trap: it is the first parameter of every one of them, and emitting it
    would make every generated tool ask the model for an argument only Python supplies.
    """
    class Tool:
        async def __call__(self, path: str, recursive: bool = False):
            """Read.

            Args:
                path: Where to read
                recursive: Descend into subdirectories
            """

    schema = dynamic.get_parameters(Tool)
    assert "self" not in schema["properties"]
    assert schema["required"] == ["path"]
    assert schema["properties"]["recursive"]["type"] == "boolean"


# --------------------------------------------------------------------------- #
# What actually goes out to a provider
# --------------------------------------------------------------------------- #
def test_the_internal_type_annotation_never_reaches_the_provider(dynamic):
    """``x-python-type`` is bookkeeping — a provider that sees it may reject the call.

    It is planted at both levels here, on the schema root and inside a property, because
    a cleaner that only pops the top level leaves one behind in every parameter and the
    rejection looks like a problem with the tool rather than with the wrapper.
    """
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string", PYTHON_TYPE_FIELD: "str"}},
        PYTHON_TYPE_FIELD: "dict",
    }
    fc = dynamic.build_function_calling("t", "desc", schema)
    parameters = fc["function"]["parameters"]
    assert PYTHON_TYPE_FIELD not in parameters
    assert PYTHON_TYPE_FIELD not in parameters["properties"]["a"]


def test_cleaning_the_schema_leaves_the_caller_copy_untouched(dynamic):
    """The caller's schema is the long-lived one and still needs the annotation to
    rebuild its args model. Cleaning in place would strip it on the first request and
    leave every later rebuild guessing the type from JSON alone."""
    schema = {"type": "object", "properties": {"a": {"type": "string", PYTHON_TYPE_FIELD: "str"}}}
    dynamic.remove_python_type_field(schema)
    assert PYTHON_TYPE_FIELD in schema["properties"]["a"]


def test_the_function_calling_shape_is_what_providers_expect(dynamic):
    """The OpenAI-style envelope — ``type``, then name/description/parameters nested
    under ``function`` — is what every provider adapter in the repo assumes. A flattened
    variant is accepted nowhere and fails identically for all of them."""
    fc = dynamic.build_function_calling("bash", "Run a command", dynamic.default_parameters_schema())
    assert fc["type"] == "function"
    assert fc["function"]["name"] == "bash"
    assert fc["function"]["description"] == "Run a command"
    assert fc["function"]["parameters"]["type"] == "object"


# --------------------------------------------------------------------------- #
# Schemas back to validating models
# --------------------------------------------------------------------------- #
def test_a_schema_becomes_a_validating_model(dynamic):
    """This model is what rejects a malformed tool call before it reaches the tool.

    Defaults have to survive the conversion and required fields have to stay required —
    a model that accepts ``Model()`` here lets a call with no query through to code that
    assumes there is one.
    """
    Model = dynamic.build_args_schema("search_tool", {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to find"},
            "limit": {"type": "integer", "default": 5},
        },
        "required": ["query"],
    })
    assert Model.__name__ == "SearchToolInput"
    assert Model(query="x").limit == 5
    with pytest.raises(Exception):
        Model()  # query is required


def test_the_recorded_python_type_beats_the_json_type(dynamic):
    """``x-python-type`` exists precisely to carry what JSON types cannot.

    JSON says only ``array``; the field says ``List[str]``. Preferring the JSON type
    would rebuild the parameter as a bare ``list`` and drop the element validation the
    original signature had.
    """
    Model = dynamic.build_args_schema("t", {
        "type": "object",
        "properties": {"names": {"type": "array", PYTHON_TYPE_FIELD: "List[str]"}},
        "required": ["names"],
    })
    assert Model(names=["a"]).names == ["a"]


def test_an_empty_schema_still_produces_a_usable_model(dynamic):
    """A no-argument tool goes down a separate branch in the builder, so it is the one
    most likely to come back as ``None`` and fail at the call site instead."""
    Model = dynamic.build_args_schema("noop", {"type": "object", "properties": {}})
    assert Model().model_dump() == {}


def test_a_model_survives_serialization_and_rebuild(dynamic):
    """Configs are persisted as JSON, so every args model makes this round trip on reload.

    The class name and the defaults both have to come back: a rebuilt model that lost its
    default turns an optional argument into a required one, and the tool starts refusing
    calls it accepted before the restart.
    """
    class Original(BaseModel):
        query: str = Field(description="What to find")
        limit: int = Field(default=5)

    rebuilt = dynamic.deserialize_args_schema(dynamic.serialize_args_schema(Original))
    assert rebuilt.__name__ == "Original"
    assert rebuilt(query="x").limit == 5
    assert rebuilt(query="x").query == "x"


# --------------------------------------------------------------------------- #
# Loading code that did not come from a file
# --------------------------------------------------------------------------- #
def test_a_class_is_loaded_from_a_source_string(dynamic):
    """The base case the whole evolution loop rests on: a string becomes a class that can
    be instantiated and called."""
    cls = dynamic.load_class("class Greeter:\n    def hi(self):\n        return 'hello'\n")
    assert cls().hi() == "hello"


def test_the_class_is_found_by_its_base_when_unnamed(dynamic):
    """Generated code names its class whatever it likes, and the caller rarely knows.

    The base class is the reliable handle — the loader looks for the one subclass in the
    module rather than requiring the caller to have parsed the source first.
    """
    class Base:
        pass

    cls = dynamic.load_class(
        "class MyTool(Base):\n    pass\n",
        base_class=Base,
        inject_imports={"Base": Base},
    )
    assert cls.__name__ == "MyTool"


def test_code_with_no_matching_class_is_rejected(dynamic):
    """A model that answered with prose, or with a bare function, produces source that
    executes fine and defines nothing usable. Saying so at load time beats registering a
    tool that is ``None``."""
    class Base:
        pass

    with pytest.raises(ValueError, match="No class found"):
        dynamic.load_class("x = 1\n", base_class=Base)


def test_a_class_that_does_not_extend_the_base_is_rejected(dynamic):
    """Named explicitly, the class is found — and still has to be checked.

    Without the subclass check the object is registered as a tool and fails later on a
    missing base-class method, at which point the source that produced it is long gone.
    """
    class Base:
        pass

    with pytest.raises(ValueError, match="not a subclass"):
        dynamic.load_class("class Other:\n    pass\n", class_name="Other", base_class=Base)


def test_a_named_class_that_is_absent_is_reported(dynamic):
    """The mismatch is between a stored config and the source it points at, so the message
    names the class that was expected rather than just failing to find something."""
    with pytest.raises(ValueError, match="Class Missing not found"):
        dynamic.load_class("class Present:\n    pass\n", class_name="Missing")


def test_a_registered_symbol_is_injected_when_the_code_uses_it(dynamic):
    """Generated code refers to framework symbols without importing them.

    Models write ``@TOOL.register_module()`` and reference base classes with no import
    line; injection is what makes that code run instead of raising ``NameError`` at exec.
    """
    sentinel = object()
    dynamic.register_symbol("SENTINEL", sentinel)
    fn = dynamic.load_function("def get():\n    return SENTINEL\n")
    assert fn() is sentinel


def test_an_unused_registered_symbol_is_not_injected(dynamic):
    """Injection is driven by what the source actually references.

    Pushing the whole registry into every module would let generated code use symbols it
    never named — and the code would then only work while loaded this way, not when the
    same source is later saved to a file and imported.
    """
    dynamic.register_symbol("UNUSED", object())
    name = dynamic.load_code("value = 1\n")
    assert not hasattr(dynamic.get_module(name), "UNUSED")


def test_a_context_provider_supplies_that_context_s_symbols(dynamic):
    """Contexts keep a tool's symbols out of an agent's namespace and vice versa, so what
    is injected depends on what kind of component is being loaded."""
    dynamic.register_context_provider("tool", lambda: {"HELPER": 42})
    cls = dynamic.load_class("class T:\n    value = HELPER\n", context="tool")
    assert cls.value == 42


def test_a_function_is_loaded_and_named_automatically(dynamic):
    """Not everything generated is a class — hooks and scoring functions arrive as plain
    ``def``s, with the caller not knowing the name either."""
    fn = dynamic.load_function("def add(a, b):\n    return a + b\n")
    assert fn(2, 3) == 5


def test_a_missing_function_name_is_reported(dynamic):
    with pytest.raises(ValueError, match="Function nope not found"):
        dynamic.load_function("def add(a, b):\n    return a + b\n", function_name="nope")


def test_the_first_class_name_is_read_off_the_source(dynamic):
    """Read by parsing, not by executing — this runs on source that may not be valid.

    Both negative cases return ``None`` rather than raising: source with no class at all,
    and source that does not parse, which is what a truncated model response looks like.
    """
    assert dynamic.extract_class_name_from_code("import os\n\nclass First:\n    pass\n") == "First"
    assert dynamic.extract_class_name_from_code("x = 1") is None
    assert dynamic.extract_class_name_from_code("class Broken(:") is None


# --------------------------------------------------------------------------- #
# The lifetime of a loaded module
# --------------------------------------------------------------------------- #
def test_loaded_modules_are_listed_and_retrievable(dynamic):
    """The listing is what teardown iterates, so a module missing from it is one nothing
    will ever unload."""
    name = dynamic.load_code("value = 7\n")
    assert name in dynamic.list_loaded_modules()
    assert dynamic.get_module(name).value == 7
    assert dynamic.get_module("never-loaded") is None


def test_unloading_removes_the_module_from_the_interpreter(dynamic):
    """Two places hold the module — the manager's own map and ``sys.modules``.

    Dropping only the first leaves the code importable process-wide, which is how a
    superseded extension version keeps answering imports after it has been replaced.
    """
    import sys

    name = dynamic.load_code("value = 1\n")
    assert name in sys.modules
    dynamic.unload_module(name)
    assert name not in sys.modules
    assert dynamic.get_module(name) is None


def test_unloading_an_unknown_module_is_harmless(dynamic):
    """Unload runs on cleanup paths where the load may have failed partway."""
    dynamic.unload_module("never-existed")  # must not raise


def test_generated_module_names_do_not_collide(dynamic):
    """Two modules sharing a name overwrite each other in ``sys.modules``: the second
    tool loaded would answer for the first, with no error anywhere."""
    names = {dynamic.load_code("value = 1\n") for _ in range(5)}
    assert len(names) == 5


def test_a_version_scoped_module_is_hot_reloaded_from_disk(dynamic, tmp_path):
    """Extensions are reloaded in place across versions; stale code must not linger.

    The module name is deliberately reused, which is what an extension upgrade does. A
    loader that saw the name already present and returned the cached module would run the
    old code for the rest of the process while reporting the new version as loaded.
    """
    path = tmp_path / "ext.py"
    path.write_text("class Ext:\n    value = 1\n")
    first = dynamic.load_class_from_path(str(path), module_name="ext.v1")
    assert first.value == 1

    path.write_text("class Ext:\n    value = 2\n")
    second = dynamic.load_class_from_path(str(path), module_name="ext.v1")
    assert second.value == 2


def test_dynamically_loaded_classes_are_recognized_as_such(dynamic):
    """Persistence depends on this: a dynamic class has no importable path, so it must be
    saved as source, while an ordinary one is saved as a module reference. Misjudging it
    either way produces a config that cannot be loaded back."""
    cls = dynamic.load_class("class Generated:\n    pass\n")
    assert dynamic.is_dynamic_class(cls) is True
    assert dynamic.is_dynamic_class(DynamicModuleManager) is False
