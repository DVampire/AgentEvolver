"""Every agent's behaviour is whatever this path produced, and its failures are quiet.

A prompt is an ``.html`` file: metadata in ``<meta>`` tags, the system prompt in
``div.system``, the user turn in ``div.user``, and ``<module src="...">`` slots that
splice in shared fragments. Nothing downstream validates the result — an agent handed an
empty system prompt runs, and answers worse.

That makes each failure here a behaviour change with no error attached. A section the
parser misses becomes ``""``. A slot that is not expanded sends a literal ``<module>``
tag to the model. An HTML entity that is not decoded shows the model ``a &amp; b``. And
the system message is cached for the life of the agent, so a prompt rewritten by an
evolution step keeps serving its old text until something explicitly reloads it.
"""

import pytest

from agentevolver.prompt.types import (
    Prompt,
    PromptConfig,
    PromptContext,
    _render_template,
    expand_modules,
    parse_prompt_file,
    parse_prompt_text,
)


def a_prompt_file(tmp_path, body, name="reason_act_agent"):
    path = tmp_path / f"{name}.html"
    path.write_text(body, encoding="utf-8")
    return str(path)


FULL = """
<html>
<head>
  <meta name="name" content="reason_act_agent">
  <meta name="description" content="Reason then act">
  <meta name="version" content="2.1.0">
  <meta name="enable_evolving" content="true">
</head>
<body>
  <div class="system">You may take {{ max_actions }} actions.</div>
  <div class="user">Task: {{ task }}</div>
</body>
</html>
"""


# --------------------------------------------------------------------------- #
# Reading a prompt file
# --------------------------------------------------------------------------- #
def test_the_metadata_block_becomes_the_config():
    """The four fields a prompt is identified and governed by, all from ``<meta>``."""
    config = parse_prompt_text(FULL)
    assert config.name == "reason_act_agent"
    assert config.description == "Reason then act"
    assert config.version == "2.1.0"
    assert config.enable_evolving is True


def test_the_two_sections_stay_separate():
    """A parser that ran the sections together would send the task in the system prompt.

    The last assertion is the one that matters: it is not enough that each template holds
    what it should, the user template must *not* have absorbed the system one. A depth
    counter that loses track of a nested ``</div>`` produces exactly that — one long
    correct-looking section and one empty one.
    """
    config = parse_prompt_text(FULL)
    assert "{{ max_actions }}" in config.system_template
    assert "{{ task }}" in config.user_template
    assert "max_actions" not in config.user_template


def test_evolving_is_off_unless_explicitly_declared():
    """A prompt that became a trainable variable by accident would be rewritten.

    The flag is read as text from an attribute, so it is off for a missing tag and matched
    case-insensitively for a present one — an author writing ``TRUE`` means yes, and
    silently treating it as no would leave a prompt out of evolution with no sign of it.
    """
    assert parse_prompt_text("<html><head></head><body></body></html>").enable_evolving is False
    assert parse_prompt_text(
        '<meta name="enable_evolving" content="TRUE">'
    ).enable_evolving is True


def test_a_missing_version_falls_back_to_a_first_version():
    """Version is compared as a string by whatever tracks prompt revisions; ``None`` breaks that."""
    assert parse_prompt_text("<html></html>").version == "1.0.0"


def test_nested_markup_inside_a_section_is_preserved():
    """The section body is inner HTML — the browser preview styles it.

    The same file is both the model's prompt and a page a human reads, so the parser
    reconstructs the inner tags verbatim rather than flattening them to text. Flattening
    would leave the prompt readable and the preview unstyled.
    """
    config = parse_prompt_text(
        '<div class="system">a<div class="rule"><b>bold</b></div>z</div>'
    )
    assert config.system_template == 'a<div class="rule"><b>bold</b></div>z'


def test_content_outside_a_known_section_is_ignored():
    """Only ``system`` and ``user`` are prompts; anything else on the page is for the reader.

    Authors put notes and layout in these files. Sweeping up every ``div`` would ship the
    author's asides to the model as instructions.
    """
    config = parse_prompt_text('<div class="notes">ignored</div><div class="system">kept</div>')
    assert config.system_template == "kept"
    assert "ignored" not in config.system_template


def test_html_entities_reach_the_model_decoded():
    """The template is prose for a model, not markup for a browser.

    It should read ``a & b``, not ``a &amp; b``. The escaping exists so the file renders
    in a browser; leaving it in place puts the escape sequences into the instructions the
    model actually follows, which is invisible in the preview and visible only in the
    model's behaviour.
    """
    config = parse_prompt_text('<div class="system">a &amp; b &#65;</div>')
    assert config.system_template == "a & b A"


def test_an_absent_section_is_empty_rather_than_missing():
    """``""`` keeps every consumer working; ``None`` makes rendering an attribute error."""
    config = parse_prompt_text('<div class="system">only system</div>')
    assert config.user_template == ""


def test_a_file_on_disk_parses_the_same_as_its_text(tmp_path):
    """The file path is the entry point every agent actually uses.

    Everything above goes through ``parse_prompt_text``; this is the one check that the
    reading half — open, decode, hand to the parser — is wired to it at all.
    """
    config = parse_prompt_file(a_prompt_file(tmp_path, FULL))
    assert config.name == "reason_act_agent"


# --------------------------------------------------------------------------- #
# Module slots
# --------------------------------------------------------------------------- #
def test_a_module_slot_is_replaced_by_the_module_body(tmp_path):
    """Shared instructions live in one file and are spliced into the prompts that want them."""
    (tmp_path / "rules.html").write_text(
        "<html><head><link rel='stylesheet' href='x.css'></head>"
        "<body><p>Batch your actions.</p></body></html>",
        encoding="utf-8",
    )
    expanded = expand_modules('<module src="rules.html"></module>', str(tmp_path))
    assert expanded == "<p>Batch your actions.</p>"


def test_a_module_s_own_head_never_reaches_the_model(tmp_path):
    """A module's CSS and scripts are for the browser preview only.

    Splicing the whole file would put a stylesheet into the middle of a system prompt —
    tokens paid for, instructions diluted, and nothing anywhere reporting a problem.
    """
    (tmp_path / "m.html").write_text(
        "<html><head><style>p{color:red}</style></head><body>text</body></html>",
        encoding="utf-8",
    )
    assert "color:red" not in expand_modules('<module src="m.html"></module>', str(tmp_path))


def test_a_module_file_without_a_body_is_used_whole(tmp_path):
    """Not every module is a full document; a bare fragment is a legitimate module.

    Requiring ``<body>`` would silently expand such a module to nothing, which reads as an
    instruction the agent simply never received.
    """
    (tmp_path / "m.html").write_text("bare fragment", encoding="utf-8")
    assert expand_modules('<module src="m.html"></module>', str(tmp_path)) == "bare fragment"


def test_a_nested_module_resolves_against_its_own_directory(tmp_path):
    """A module's own slots are relative to where that module lives, not to the prompt.

    Resolving against the top-level prompt directory instead would make a module's
    correctness depend on which prompt included it — it works for one caller and raises a
    missing-file error for the next.
    """
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "inner.html").write_text("<body>inner</body>", encoding="utf-8")
    (tmp_path / "sub" / "outer.html").write_text(
        '<body>[<module src="inner.html"></module>]</body>', encoding="utf-8",
    )
    assert expand_modules('<module src="sub/outer.html"></module>', str(tmp_path)) == "[inner]"


def test_several_slots_are_all_expanded(tmp_path):
    """A regex that stopped after the first match would leave later slots as literal tags."""
    for name in ("a", "b"):
        (tmp_path / f"{name}.html").write_text(f"<body>{name}</body>", encoding="utf-8")
    expanded = expand_modules(
        '<module src="a.html"></module>|<module src="b.html"></module>', str(tmp_path),
    )
    assert expanded == "a|b"


def test_attribute_order_does_not_matter(tmp_path):
    """Authors add ``id`` and ``data-`` attributes for the preview to hook onto.

    A pattern anchored on ``src`` being the first attribute would fail on those and leave
    the tag unexpanded — an authoring choice with no visible connection to the breakage.
    """
    (tmp_path / "m.html").write_text("<body>ok</body>", encoding="utf-8")
    assert expand_modules('<module id="x" src="m.html" data-y="1"></module>', str(tmp_path)) == "ok"


def test_a_cyclic_module_is_refused_rather_than_recursing_forever(tmp_path):
    """Two modules that include each other are an easy mistake and a hang, not a crash.

    Expansion is recursive with no natural base case, so without the depth cap the agent's
    startup never returns and there is nothing in the log to say why.
    """
    (tmp_path / "loop.html").write_text(
        '<body><module src="loop.html"></module></body>', encoding="utf-8",
    )
    with pytest.raises(ValueError, match="max depth"):
        expand_modules('<module src="loop.html"></module>', str(tmp_path))


def test_slots_are_expanded_when_a_prompt_file_is_read(tmp_path):
    """Expansion has to happen on the read path, not be something a caller remembers.

    Every agent loads its prompt through ``parse_prompt_file``. If the splice lived
    anywhere else, one caller would forget it and send a literal ``<module>`` tag to the
    model as if it were an instruction.
    """
    (tmp_path / "rules.html").write_text("<body>BATCH</body>", encoding="utf-8")
    path = a_prompt_file(tmp_path, '<div class="system"><module src="rules.html"></module></div>')
    config = parse_prompt_file(path)
    assert config.system_template == "BATCH"
    assert "<module" not in config.system_template


def test_text_with_no_slots_passes_through_untouched(tmp_path):
    """Most prompts have no modules at all; expansion must be a no-op for them."""
    assert expand_modules("plain text", str(tmp_path)) == "plain text"


# --------------------------------------------------------------------------- #
# Rendering, and what may be cached
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_system_message_renders_its_variables():
    prompt = Prompt(name="p", system_template="You may take {{ max_actions }} actions.")
    message = await prompt.get_system_message({"max_actions": 10})
    assert message.text == "You may take 10 actions."
    assert message.role == "system"


@pytest.mark.asyncio
async def test_the_user_message_is_a_text_content_part():
    """It is built as a part list, not a bare string, so media can be appended later.

    Reading it back through ``.text`` is how every consumer sees it, which is why the
    assertion goes through the projection rather than through ``content``.
    """
    prompt = Prompt(name="p", user_template="Task: {{ task }}")
    message = await prompt.get_user_message({"task": "fix it"})
    assert message.role == "user"
    assert message.text == "Task: fix it"


@pytest.mark.asyncio
async def test_static_defaults_fill_in_what_the_caller_omits():
    """The prompt file's own variables are the floor; the call site overrides, never resets.

    If the caller's dict replaced the defaults instead of merging into them, a caller that
    supplies one variable would blank every other one — and the missing ones render empty
    rather than raising.
    """
    prompt = Prompt(name="p", system_template="{{ a }}/{{ b }}", variables={"a": "1", "b": "2"})
    assert (await prompt.get_system_message({"b": "override"})).text == "1/override"


@pytest.mark.asyncio
async def test_rendering_does_not_mutate_the_static_defaults():
    """The merge has to build a new dict, because the prompt outlives the render.

    Updating ``self.variables`` in place would make the first call's overrides stick for
    the rest of the agent's life, and the symptom is a later step rendering a value nobody
    passed it.
    """
    prompt = Prompt(name="p", system_template="{{ a }}", variables={"a": "1"})
    await prompt.get_system_message({"a": "2"})
    assert prompt.variables == {"a": "1"}


@pytest.mark.asyncio
async def test_the_system_message_is_cached_between_calls():
    """Assembled once per agent and reused every step — re-rendering it is waste.

    The identity check is the point: an equal-but-new object would mean the template is
    re-rendered on every step of every run, and would break the Anthropic cache breakpoint
    that depends on the same prefix object being sent each time.
    """
    prompt = Prompt(name="p", system_template="{{ a }}")
    first = await prompt.get_system_message({"a": "1"})
    assert (await prompt.get_system_message({"a": "2"})) is first


@pytest.mark.asyncio
async def test_a_reload_defeats_the_cache_after_an_evolution_step():
    """Evolution rewrites the template in place; without this the agent never sees it.

    The cache is what makes the rewrite invisible — the run continues with the old system
    prompt and reports as if the new one were in effect, so an evolution step that changed
    nothing and one that was never picked up look identical.
    """
    prompt = Prompt(name="p", system_template="{{ a }}")
    await prompt.get_system_message({"a": "1"})
    assert (await prompt.get_system_message({"a": "2"}, reload=True)).text == "2"


@pytest.mark.asyncio
async def test_the_user_message_is_re_rendered_every_turn():
    """It carries the turn's task; caching it would repeat the first one forever.

    This is the same mechanism as the system message with the opposite correct answer,
    which is why both are pinned: the two look symmetric and are not.
    """
    prompt = Prompt(name="p", user_template="{{ task }}")
    assert (await prompt.get_user_message({"task": "a"})).text == "a"
    assert (await prompt.get_user_message({"task": "b"})).text == "b"


def test_an_unfilled_variable_renders_empty_rather_than_leaving_the_marker():
    """A leftover ``{{ missing }}`` in a prompt is markup the model reads as instruction."""
    assert _render_template("[{{ missing }}]", {}) == "[]"


def test_a_template_with_no_variables_is_unchanged():
    """Extra render context is normal — callers pass one dict to many templates."""
    assert _render_template("no variables here", {"unused": 1}) == "no variables here"


# --------------------------------------------------------------------------- #
# The config that is stored and reloaded
# --------------------------------------------------------------------------- #
def test_a_config_converts_to_a_renderable_prompt():
    """``PromptConfig`` is the stored form; ``Prompt`` is the one that renders.

    The conversion is where a field gets forgotten — the templates arrive and the
    variables do not, so rendering succeeds and quietly substitutes nothing.
    """
    prompt = PromptConfig(name="p", system_template="s", variables={"a": 1}).to_prompt()
    assert isinstance(prompt, Prompt)
    assert prompt.system_template == "s"
    assert prompt.variables == {"a": 1}


def test_a_config_round_trips_through_its_serialised_form():
    """``model_dump`` and ``model_validate`` are both hand-written here, so they can drift.

    Neither is pydantic's own: a field added to the class but to only one of the two
    methods is dropped on the way through, and the loss shows up as a prompt that reverted
    to its defaults after being saved.
    """
    original = PromptConfig(name="p", version="2.0.0", enable_evolving=True,
                            system_template="s", user_template="u", metadata={"k": "v"})
    restored = PromptConfig.model_validate(original.model_dump())
    assert restored.model_dump() == original.model_dump()


def test_deserialising_a_partial_record_fills_the_defaults():
    """Stored records predate later fields; the safe value for each has to be the default.

    ``permission_mode`` is the one that matters: a record written before the field existed
    must come back as ``workspace_write``, not as an empty string that no permission check
    recognises.
    """
    config = PromptConfig.model_validate({"name": "p"})
    assert config.version == "1.0.0"
    assert config.enable_evolving is False
    assert config.permission_mode == "workspace_write"
    assert config.variables == {}


def test_null_collections_serialise_as_empty_ones():
    """A ``None`` here would break every consumer that iterates them.

    Both fields are ``Optional`` and both are read as mappings everywhere else, so the
    dump normalises them rather than leaving each reader to guard.
    """
    dumped = PromptConfig(name="p", variables=None, metadata=None).model_dump()
    assert dumped["variables"] == {} and dumped["metadata"] == {}


def test_a_prompt_context_needs_nothing_to_be_constructed():
    """Callers build it incrementally, so every field has to have a usable default."""
    assert PromptContext().input == {}
