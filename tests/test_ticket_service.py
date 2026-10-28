"""Ticket helpers that don't need Discord or Postgres."""

import hikari
import pytest

from serenity.services.ticket_service import TicketError, is_staff, parse_questions


def test_parse_questions():
    fields = parse_questions(" Order number | What went wrong? | order number ")
    assert [f["key"] for f in fields] == ["order_number", "what_went_wrong", "order_number_"]
    assert fields[1]["label"] == "What went wrong?"
    assert parse_questions("") == []
    with pytest.raises(TicketError):
        parse_questions("|".join("q" for _ in range(6)))
    with pytest.raises(TicketError):
        parse_questions("x" * 46)


def test_is_staff():
    ticket_type = {"staff_role_ids": [1, 2]}
    none = hikari.Permissions.NONE
    assert is_staff(ticket_type, [3, 2], none)
    assert not is_staff(ticket_type, [3], none)
    assert is_staff(ticket_type, [], hikari.Permissions.MANAGE_GUILD)


def _service(rest=None, repo=None):
    from unittest.mock import AsyncMock, MagicMock

    from hikari.impl import MessageActionRowBuilder

    from serenity.services.ticket_service import TicketService

    rest = rest or AsyncMock()
    rest.build_message_action_row = MagicMock(side_effect=MessageActionRowBuilder)
    return TicketService(repo or AsyncMock(), rest, MagicMock(), AsyncMock())


def _types(n):
    return [{"id": i, "name": f"T{i}", "emoji": None, "description": None} for i in range(1, n + 1)]


def test_panel_buttons_wrap_at_five_per_row():
    panel = {"id": 9, "title": "Help", "description": None, "style": "buttons"}
    _, rows = _service().build_panel(panel, _types(7))
    built = [row.build()[0]["components"] for row in rows]
    assert [len(r) for r in built] == [5, 2]
    assert built[1][1]["custom_id"] == "ticket:open:7"


def test_panel_menu_lists_types():
    panel = {"id": 9, "title": "Help", "description": None, "style": "menu"}
    _, rows = _service().build_panel(panel, _types(3))
    (menu,) = rows[0].build()[0]["components"]
    assert menu["custom_id"] == "ticket:menu:9"
    assert [o["value"] for o in menu["options"]] == ["1", "2", "3"]


@pytest.mark.asyncio
async def test_panel_deleted_by_hand_is_reposted():
    from unittest.mock import AsyncMock, MagicMock

    rest = AsyncMock()
    rest.edit_message.side_effect = hikari.NotFoundError("", {}, b"")
    rest.create_message.return_value = MagicMock(id=555)
    repo = AsyncMock()
    repo.list_types.return_value = _types(1)
    service = _service(rest, repo)
    service.panel_messages.add(3)
    panel = {"id": 9, "guild_id": 1, "channel_id": 2, "message_id": 3, "title": "Help",
             "description": None, "style": "buttons", "type_ids": [1]}

    await service.post_panel(panel)
    repo.update_panel.assert_awaited_once_with(1, 9, message_id=555)
    assert service.panel_messages == {555}  # the dead message id 3 isn't kept

    await service.panel_message_deleted(555)
    repo.forget_panel_message.assert_awaited_once_with(555)
    await service.panel_message_deleted(1234)  # not a panel: no query
    assert repo.forget_panel_message.await_count == 1


def test_render_channel_name():
    from serenity.services.ticket_service import overflow_name, render_channel_name

    def render(template, **fields):
        return render_channel_name(
            template, type_name="Billing Help", number=12, username="Heet.P", fields=fields
        )

    assert render("{type}-{number}") == "billing-help-12"
    assert render("{user} {field:order}", order="#A-99 x") == "heet-p-a-99-x"
    assert render("{field:missing}🎫") == "ticket-12"  # emoji are dropped with other symbols
    assert render("!!!") == "ticket-12"
    assert len(render("x" * 200)) == 100
    assert [overflow_name("Support", i) for i in (1, 2)] == ["Support", "Support 2"]


@pytest.mark.asyncio
async def test_duplicate_type_name_is_a_friendly_error():
    from unittest.mock import AsyncMock, MagicMock

    import asyncpg

    from serenity.extensions.tickets import on_error

    ctx = MagicMock(respond=AsyncMock())
    await on_error(ctx, asyncpg.UniqueViolationError("duplicate key"))
    assert "already exists" in ctx.respond.call_args.args[0]


@pytest.mark.asyncio
async def test_one_broken_panel_doesnt_stop_the_refresh():
    from unittest.mock import AsyncMock

    rest = AsyncMock()
    rest.edit_message.side_effect = [hikari.BadRequestError("", {}, b""), None]
    repo = AsyncMock()
    repo.list_types.return_value = _types(1)
    panel = {"guild_id": 1, "channel_id": 2, "title": "Help", "description": None,
             "style": "buttons", "type_ids": [1]}
    repo.list_panels.return_value = [{**panel, "id": 1, "message_id": 10}, {**panel, "id": 2, "message_id": 11}]
    await _service(rest, repo).refresh_panels(1)
    assert rest.edit_message.await_count == 2


@pytest.mark.asyncio
async def test_duplicate_types_on_a_panel_are_listed_once():
    from unittest.mock import AsyncMock

    from serenity.extensions.tickets import resolve_type_list

    service = AsyncMock()
    service.resolve_type.side_effect = lambda _, name: {"id": {"a": 1, "b": 2}[name]}
    assert await resolve_type_list(service, 1, "a, b, a") == [1, 2]


@pytest.mark.asyncio
async def test_tickets_module_cant_be_enabled_outside_the_allowlist(monkeypatch):
    from unittest.mock import AsyncMock

    from serenity.core.modules import ModuleManager, ModuleType
    from serenity.utils.errors import ConfigurationError

    repo = AsyncMock()
    manager = ModuleManager(repo)
    monkeypatch.setenv("TICKETS_GUILDS", "1")
    await manager.enable_module(1, ModuleType.TICKETS)
    with pytest.raises(ConfigurationError, match="isn't available"):
        await manager.enable_module(2, ModuleType.TICKETS)
    repo.set_module_enabled.assert_awaited_once()


IGN = r"[A-Za-z0-9_]{3,16}"


def test_typed_fields_are_checked_with_friendly_errors():
    from serenity.services.ticket_service import check_answers, set_field

    fields = set_field([], "Minecraft IGN", "pattern", options=IGN, identity=True)
    fields = set_field(fields, "Budget", "number", required=False)
    fields = set_field(fields, "Due", "date", required=False)
    fields = set_field(fields, "Region", "select", options="EU, NA, EU")
    fields = set_field(fields, "Link", "url", required=False)
    assert fields[3]["options"] == ["EU", "NA"]

    ok = check_answers(fields, {"minecraft_ign": "Notch_2", "budget": "1,000", "region": "na"})
    assert ok == {"minecraft_ign": "Notch_2", "budget": "1,000", "due": "", "region": "NA", "link": ""}

    with pytest.raises(TicketError) as exc:
        check_answers(fields, {"minecraft_ign": "not an ign!", "budget": "lots", "due": "soon",
                               "region": "Mars", "link": "example.com"})
    problems = str(exc.value).splitlines()
    assert len(problems) == 5 and "Minecraft IGN" in problems[0] and "EU, NA" in problems[3]
    with pytest.raises(TicketError, match="Please answer \\*\\*Region"):
        check_answers(fields, {"minecraft_ign": "Notch"})


def test_set_field_replaces_by_label_and_enforces_limits():
    from serenity.services.ticket_service import check_answers, set_field

    fields = set_field([], "Order", "short")
    fields = set_field(fields, "order", "number", staff_only=True)  # replaced, key kept
    assert len(fields) == 1 and fields[0]["key"] == "order" and fields[0]["required"] is False
    assert check_answers([set_field([], "Who", "user")[0]], {"who": "<@!123>"}) == {"who": "123"}

    with pytest.raises(TicketError, match="regular expression"):
        set_field([], "IGN", "pattern", options="([")
    with pytest.raises(TicketError, match="dropdown needs"):
        set_field([], "Region", "select")
    picks: list[dict] = []
    for i in range(4):
        picks = set_field(picks, f"Pick {i}", "user")
    with pytest.raises(TicketError, match="at most 4"):
        set_field(picks, "Pick 5", "user")
    assert len(set_field(picks, "Pick 5", "user", staff_only=True)) == 5  # never asked: no menu
    with pytest.raises(TicketError, match="at most 5"):
        set_field(set_field(picks, "Pick 5", "short"), "Six", "short")


@pytest.mark.asyncio
async def test_dropdown_questions_follow_the_form(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from serenity.extensions import tickets
    from serenity.services.ticket_service import set_field

    fields = set_field(set_field([], "IGN", "pattern", options=IGN), "Region", "select", options="EU,NA")
    ticket_type = {"id": 7, "name": "Appeal", "fields": fields}
    service = _service()
    service.repo.get_type.return_value = ticket_type
    created = AsyncMock()
    monkeypatch.setattr(tickets, "create_and_confirm", created)

    form = MagicMock(spec=hikari.ModalInteraction, guild_id=1, user=MagicMock(id=2))
    form.create_initial_response = AsyncMock()
    monkeypatch.setattr(tickets, "modal_values", lambda _: {"ign": "bad ign!"})
    with pytest.raises(TicketError, match="IGN"):  # checked before the dropdowns are shown
        await tickets.submit_form(form, service, 7)
    monkeypatch.setattr(tickets, "modal_values", lambda _: {"ign": "Notch"})
    await tickets.submit_form(form, service, 7)
    rows = form.create_initial_response.call_args.kwargs["components"]
    assert [r.build()[0]["components"][0]["custom_id"] for r in rows] == ["ticket:pick:7:region", "ticket:submit:7"]

    press = MagicMock(spec=hikari.ComponentInteraction, guild_id=1, user=MagicMock(id=2))
    with pytest.raises(TicketError, match="Region"):
        await tickets.submit_picks(press, service, 7)
    service.get_draft((1, 2, 7))["region"] = "NA"  # what the pick handler stores

    created.side_effect = TicketError("You already have an open **Appeal** ticket.")
    with pytest.raises(TicketError, match="already have"):
        await tickets.submit_picks(press, service, 7)
    assert service.get_draft((1, 2, 7))["ign"] == "Notch"  # refused: the answers are kept for a retry

    created.side_effect = None
    await tickets.submit_picks(press, service, 7)
    assert created.call_args.args[3] == {"ign": "Notch", "region": "NA"}
    assert service.drafts == {}


@pytest.mark.parametrize(
    "pattern, slow",
    [
        (IGN, False),
        (r"^\d{4}-\d{2}$", False),
        (r"(ab)+", False),
        (r"([A-Za-z]+)*[0-9]$", True),  # repeat inside a repeat: exponential
        (r"(\w+\s?)+", True),
        (r"(foo|bar)+", True),  # | inside a repeat
        (r"(\w)\1", True),  # backreference
        (r".*.*.*.*x", True),  # too many open-ended repeats
        (r"(a+){6}b", True),  # inside a bounded repeat: still exponential
        (r"(a{0,2}){16}b", True),
        (r"(a|aa){8}b", True),
        (r"(a?){30}b", True),  # an optional inside a repeat: two paths per iteration
        (r"(?:a?){30}b", True),
        (r"(?>(?:a?){30})b", True),  # atomic groups are walked too
        (r"((a?){30}){2}b", True),
        (r"(a?){2,}b", True),
        (r"(\d{3}-)+\d", False),
        ("a?" * 7 + "b", True),  # a chain of optionals: 2^n paths without any nesting
        ("a{0,100}" * 4 + "b", True),  # wide bounded repeats count like `*`
        ("[a-z]{0,100}" * 6 + "b", True),
        (r"^https?://(www\.)?[\w.-]+(/\S*)?$", False),
        (r"(\d{3}-){2}\d{4}", False),  # a fixed-size repeat inside a bounded one is fine
    ],
)
def test_slow_patterns_are_refused(pattern, slow):
    from serenity.services.ticket_service import set_field

    if slow:
        with pytest.raises(TicketError, match="too long to check"):
            set_field([], "IGN", "pattern", options=pattern)
    else:
        assert set_field([], "IGN", "pattern", options=pattern)[0]["pattern"] == pattern


def test_type_field_option_choices_and_defaults():
    import inspect

    from serenity.extensions import tickets
    from serenity.services.ticket_service import FIELD_TYPES

    assert tickets.type_field.options["kind"].choices == tickets.FIELD_KINDS
    assert sorted(tickets.FIELD_KINDS.values()) == sorted(FIELD_TYPES)  # every stored kind, once
    assert tickets.FIELD_KINDS["Dropdown"] == "select" and tickets.FIELD_KINDS["Member"] == "user"
    params = inspect.signature(tickets.type_field.callback).parameters
    defaults = {n: params[n].default for n in ("kind", "required", "identity", "staff_only", "remove")}
    # None keeps an existing field's setting; set_field fills in a new field's defaults.
    assert defaults == {"kind": None, "required": None, "identity": None, "staff_only": None, "remove": False}


def test_changing_a_field_keeps_what_isnt_given():
    from serenity.services.ticket_service import set_field

    (ign,) = set_field([], "IGN", "pattern", options=IGN, identity=True, required=False)
    (changed,) = set_field([ign], "ign", staff_only=True)  # the label is matched case-insensitively
    assert changed == {**ign, "label": "ign", "staff_only": True}  # kind, pattern, identity kept
    (back,) = set_field([changed], "IGN", staff_only=False)
    assert back["required"] is True and back["identity"] is True  # asked again: required by default
    (region,) = set_field([], "Region", "select", options="EU,NA")
    assert set_field([region], "Region", required=False)[0]["options"] == ["EU", "NA"]
    with pytest.raises(TicketError, match="dropdown needs"):  # a new kind needs its own options
        set_field([ign], "IGN", "select")
    (new,) = set_field([], "Order")
    assert (new["type"], new["required"], "identity" in new) == ("short", True, False)


def test_answers_to_removed_fields_stay_staff_only():
    from unittest.mock import MagicMock

    from hikari.impl import ModalActionRowBuilder

    from serenity.extensions.tickets import text_inputs
    from serenity.services.ticket_service import set_field

    service = _service()
    service.rest.build_modal_action_row = MagicMock(side_effect=ModalActionRowBuilder)
    order = set_field([], "Order")
    ticket = {"fields": {"order": "A-1", "verdict": "banned"}}  # Verdict was staff-only, now removed
    embed = service.show_answers(hikari.Embed(), ticket, {"fields": order})
    assert [(f.name, f.value) for f in embed.fields] == [("Order", "A-1")]

    long_answer = {"order": "x" * 500}  # given when Order was a paragraph
    (row,) = text_inputs(service, order, long_answer)
    assert len(row.build()[0]["components"][0]["value"]) == 100


def test_drafts_expire_and_are_capped(monkeypatch):
    from serenity.services import ticket_service

    clock = [1000.0]
    monkeypatch.setattr(ticket_service.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(ticket_service, "MAX_DRAFTS", 3)
    service = _service()

    service.save_draft((1, 1, 1), {"a": "1"})
    clock[0] += ticket_service.DRAFT_TTL
    with pytest.raises(TicketError, match="expired"):
        service.get_draft((1, 1, 1))
    for user in (2, 3, 4):
        service.save_draft((1, user, 1), {})
    assert list(service.drafts) == [(1, 2, 1), (1, 3, 1), (1, 4, 1)]  # the expired one was dropped
    service.save_draft((1, 5, 1), {})
    assert list(service.drafts) == [(1, 3, 1), (1, 4, 1), (1, 5, 1)]  # past the cap, oldest goes
    service.save_draft((1, 3, 1), {"b": "2"})  # a new form replaces the member's old one
    assert list(service.drafts) == [(1, 4, 1), (1, 5, 1), (1, 3, 1)]
    assert service.get_draft((1, 3, 1)) == {"b": "2"}


def test_staff_only_answers_stay_out_of_the_openers_copy():
    from serenity.services.ticket_service import render_transcript

    document = {
        "ticket": {"number": 1, "type": "Support", "opener_id": 1, "opened_at": 0},
        "fields": {"Order": "A-1"},
        "staff_fields": {"Refund approved": "yes"},
        "messages": [],
    }
    assert "Refund approved" not in render_transcript(document, staff=False)
    assert "Refund approved (staff): yes" in render_transcript(document, staff=True)
