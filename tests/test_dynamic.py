"""Dynamic module manager: turning generated source into live, callable objects.

This is the machinery a self-evolving system runs on — an LLM writes a tool
class as a string and this module has to make it importable, work out its
parameter schema from the signature, and hand the model a function-calling
description that strict providers accept. Two properties get special attention
because breaking them is silent: array schemas must declare ``items`` (Gemini
rejects them otherwise), and the internal ``x-python-type`` annotation must
never reach the provider.
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
    manager = DynamicModuleManager()
    yield manager
    for name in manager.list_loaded_modules():
        manager.unload_module(name)


# ------------------------------------------------------------ type mapping
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
    assert dynamic.annotation_to_types(annotation) == (json_type, python_type)


def test_an_unannotated_parameter_is_treated_as_a_string(dynamic):
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
    """Strict consumers (the Gemini API) reject an array schema without ``items``."""
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
    assert dynamic.parse_type_string(text) is expected


def test_parametrized_type_strings_round_trip(dynamic):
    assert dynamic.parse_type_string("Optional[str]") == Optional[str]
    assert dynamic.parse_type_string("List[int]") == List[int]
    assert dynamic.parse_type_string("Dict[str, int]") == Dict[str, int]


def test_an_unparseable_type_string_degrades_to_any(dynamic):
    """Generated code carries anything; an unknown type must not stop the load."""
    assert dynamic.parse_type_string("SomeUserClass") is Any
    assert dynamic.parse_type_string("Dict[nonsense]") is dict


# ------------------------------------------------------------- docstrings
def test_google_style_argument_descriptions_are_extracted(dynamic):
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
    """``Returns:`` content must not be mistaken for another parameter."""
    descriptions = dynamic.parse_docstring_descriptions("""
        Args:
            a: first

        Returns:
            value: not a parameter
    """)
    assert descriptions == {"a": "first"}


@pytest.mark.parametrize("docstring", ["", None, "Just a summary with no Args section."])
def test_a_docstring_without_arguments_yields_nothing(dynamic, docstring):
    assert dynamic.parse_docstring_descriptions(docstring) == {}


# -------------------------------------------------------- parameter schema
def test_a_signature_becomes_a_parameter_schema(dynamic):
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
    def tag(names: List[str]):
        """Tag things.

        Args:
            names: The tags
        """

    assert dynamic.get_parameters(tag)["properties"]["names"]["items"] == {"type": "string"}


def test_varargs_are_left_out_of_the_schema(dynamic):
    """``*args``/``**kwargs`` are not things the model can be asked to fill in."""
    def flexible(a: str, *args, **kwargs):
        pass

    assert list(dynamic.get_parameters(flexible)["properties"]) == ["a"]


def test_a_parameterless_callable_gets_the_empty_schema(dynamic):
    def nothing():
        pass

    assert dynamic.get_parameters(nothing) == dynamic.default_parameters_schema()


def test_a_class_is_described_by_its_call_signature(dynamic):
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


# -------------------------------------------------- function-calling output
def test_the_internal_type_annotation_never_reaches_the_provider(dynamic):
    """``x-python-type`` is bookkeeping — a provider that sees it may reject the call."""
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
    schema = {"type": "object", "properties": {"a": {"type": "string", PYTHON_TYPE_FIELD: "str"}}}
    dynamic.remove_python_type_field(schema)
    assert PYTHON_TYPE_FIELD in schema["properties"]["a"]


def test_the_function_calling_shape_is_what_providers_expect(dynamic):
    fc = dynamic.build_function_calling("bash", "Run a command", dynamic.default_parameters_schema())
    assert fc["type"] == "function"
    assert fc["function"]["name"] == "bash"
    assert fc["function"]["description"] == "Run a command"
    assert fc["function"]["parameters"]["type"] == "object"


# ----------------------------------------------------------- args schema
def test_a_schema_becomes_a_validating_model(dynamic):
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
    """``x-python-type`` exists precisely to carry what JSON types cannot."""
    Model = dynamic.build_args_schema("t", {
        "type": "object",
        "properties": {"names": {"type": "array", PYTHON_TYPE_FIELD: "List[str]"}},
        "required": ["names"],
    })
    assert Model(names=["a"]).names == ["a"]


def test_an_empty_schema_still_produces_a_usable_model(dynamic):
    Model = dynamic.build_args_schema("noop", {"type": "object", "properties": {}})
    assert Model().model_dump() == {}


def test_a_model_survives_serialization_and_rebuild(dynamic):
    class Original(BaseModel):
        query: str = Field(description="What to find")
        limit: int = Field(default=5)

    rebuilt = dynamic.deserialize_args_schema(dynamic.serialize_args_schema(Original))
    assert rebuilt.__name__ == "Original"
    assert rebuilt(query="x").limit == 5
    assert rebuilt(query="x").query == "x"


# ------------------------------------------------------------ loading code
def test_a_class_is_loaded_from_a_source_string(dynamic):
    cls = dynamic.load_class("class Greeter:\n    def hi(self):\n        return 'hello'\n")
    assert cls().hi() == "hello"


def test_the_class_is_found_by_its_base_when_unnamed(dynamic):
    class Base:
        pass

    cls = dynamic.load_class(
        "class MyTool(Base):\n    pass\n",
        base_class=Base,
        inject_imports={"Base": Base},
    )
    assert cls.__name__ == "MyTool"


def test_code_with_no_matching_class_is_rejected(dynamic):
    class Base:
        pass

    with pytest.raises(ValueError, match="No class found"):
        dynamic.load_class("x = 1\n", base_class=Base)


def test_a_class_that_does_not_extend_the_base_is_rejected(dynamic):
    class Base:
        pass

    with pytest.raises(ValueError, match="not a subclass"):
        dynamic.load_class("class Other:\n    pass\n", class_name="Other", base_class=Base)


def test_a_named_class_that_is_absent_is_reported(dynamic):
    with pytest.raises(ValueError, match="Class Missing not found"):
        dynamic.load_class("class Present:\n    pass\n", class_name="Missing")


def test_a_registered_symbol_is_injected_when_the_code_uses_it(dynamic):
    """Generated code refers to framework symbols without importing them."""
    sentinel = object()
    dynamic.register_symbol("SENTINEL", sentinel)
    fn = dynamic.load_function("def get():\n    return SENTINEL\n")
    assert fn() is sentinel


def test_an_unused_registered_symbol_is_not_injected(dynamic):
    dynamic.register_symbol("UNUSED", object())
    name = dynamic.load_code("value = 1\n")
    assert not hasattr(dynamic.get_module(name), "UNUSED")


def test_a_context_provider_supplies_that_context_s_symbols(dynamic):
    dynamic.register_context_provider("tool", lambda: {"HELPER": 42})
    cls = dynamic.load_class("class T:\n    value = HELPER\n", context="tool")
    assert cls.value == 42


def test_a_function_is_loaded_and_named_automatically(dynamic):
    fn = dynamic.load_function("def add(a, b):\n    return a + b\n")
    assert fn(2, 3) == 5


def test_a_missing_function_name_is_reported(dynamic):
    with pytest.raises(ValueError, match="Function nope not found"):
        dynamic.load_function("def add(a, b):\n    return a + b\n", function_name="nope")


def test_the_first_class_name_is_read_off_the_source(dynamic):
    assert dynamic.extract_class_name_from_code("import os\n\nclass First:\n    pass\n") == "First"
    assert dynamic.extract_class_name_from_code("x = 1") is None
    assert dynamic.extract_class_name_from_code("class Broken(:") is None


# --------------------------------------------------------------- lifecycle
def test_loaded_modules_are_listed_and_retrievable(dynamic):
    name = dynamic.load_code("value = 7\n")
    assert name in dynamic.list_loaded_modules()
    assert dynamic.get_module(name).value == 7
    assert dynamic.get_module("never-loaded") is None


def test_unloading_removes_the_module_from_the_interpreter(dynamic):
    import sys

    name = dynamic.load_code("value = 1\n")
    assert name in sys.modules
    dynamic.unload_module(name)
    assert name not in sys.modules
    assert dynamic.get_module(name) is None


def test_unloading_an_unknown_module_is_harmless(dynamic):
    dynamic.unload_module("never-existed")  # must not raise


def test_generated_module_names_do_not_collide(dynamic):
    names = {dynamic.load_code("value = 1\n") for _ in range(5)}
    assert len(names) == 5


def test_a_version_scoped_module_is_hot_reloaded_from_disk(dynamic, tmp_path):
    """Extensions are reloaded in place across versions; stale code must not linger."""
    path = tmp_path / "ext.py"
    path.write_text("class Ext:\n    value = 1\n")
    first = dynamic.load_class_from_path(str(path), module_name="ext.v1")
    assert first.value == 1

    path.write_text("class Ext:\n    value = 2\n")
    second = dynamic.load_class_from_path(str(path), module_name="ext.v1")
    assert second.value == 2


def test_dynamically_loaded_classes_are_recognized_as_such(dynamic):
    cls = dynamic.load_class("class Generated:\n    pass\n")
    assert dynamic.is_dynamic_class(cls) is True
    assert dynamic.is_dynamic_class(DynamicModuleManager) is False
