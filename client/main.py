"""Graphical launcher and entry point for the multiplayer client."""
from __future__ import annotations

import argparse
import asyncio
import math
import os
from dataclasses import dataclass
from typing import Optional

import httpx
from dotenv import load_dotenv

from .api import AuthSession, ServerClient
from .game import GameClient


def http_to_ws(url: str) -> str:
    if url.startswith("https://"):
        return "wss://" + url[len("https://") :]
    if url.startswith("http://"):
        return "ws://" + url[len("http://") :]
    return url


load_dotenv()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Launch the Nigh.ty multiplayer client")
    parser.add_argument("--server", default=os.getenv("SERVER_BASE_URL", "http://localhost:8000"), help="Base HTTP URL of the server")
    parser.add_argument("--ws", default=os.getenv("SERVER_WS_URL"), help="Override WebSocket URL (defaults to server converted to ws://)")
    parser.add_argument(
        "--username",
        default=os.getenv("PLAYER_NAME", ""),
        help="Default player display name shown on the login screen",
    )
    parser.add_argument(
        "--password",
        default=os.getenv("PLAYER_PASSWORD", ""),
        help="Default password used on the login screen (useful for local testing)",
    )
    return parser


@dataclass
class LoginResult:
    session: AuthSession
    requested_world: Optional[str]


class ClientApplication:
    """Interactive pygame application handling authentication and world selection."""

    WINDOW_SIZE = (960, 640)
    BG_COLOR = (10, 12, 28)
    PANEL_COLOR = (24, 28, 56)
    ACCENT_COLOR = (120, 162, 255)
    TEXT_COLOR = (230, 235, 255)
    ERROR_COLOR = (255, 120, 120)
    LIST_ITEM_HEIGHT = 72

    def __init__(self, client: ServerClient, *, default_username: str = "", default_password: str = "") -> None:
        self._client = client
        self._default_username = default_username
        self._default_password = default_password

    async def run(self, resume_session: Optional[AuthSession] = None) -> Optional[LoginResult]:
        # Import pygame lazily so automated tests that don't have SDL can still import the module.
        import pygame

        pygame.init()
        pygame.font.init()
        screen = pygame.display.set_mode(self.WINDOW_SIZE)
        pygame.display.set_caption("Nigh.ty Multiplayer Client")
        clock = pygame.time.Clock()

        try:
            session = resume_session
            if session is None:
                session = await self._auth_screen(pygame, screen, clock)
                if session is None:
                    return None

            world_id = await self._menu_screen(pygame, screen, clock, session)
            if world_id is None:
                return None

            return LoginResult(session=session, requested_world=world_id)
        finally:
            pygame.display.quit()
            pygame.quit()

    async def _auth_screen(self, pygame_module, screen, clock) -> Optional[AuthSession]:
        pygame = pygame_module
        background_phase = 0.0

        layout = self._auth_layout(pygame, screen.get_size())
        username_input = TextInput(layout["username_rect"], "Username", self._default_username, max_length=32)
        password_input = TextInput(
            layout["password_rect"], "Password", self._default_password, masked=True, max_length=128
        )
        inputs = [username_input, password_input]
        focused_index = 0
        inputs[focused_index].focused = True

        login_button = Button(layout["login_rect"], "Sign in")
        quit_button = Button(layout["quit_rect"], "Exit")

        pygame.key.start_text_input()
        pygame.key.set_text_input_rect(inputs[focused_index].rect)

        status_message: Optional[str] = None
        status_color = self.TEXT_COLOR
        login_task: Optional[asyncio.Task[AuthSession]] = None

        try:
            while True:
                dt = clock.tick(60) / 1000.0
                background_phase += dt * 0.6

                # Ensure controls follow the window size so the UI stays responsive.
                layout = self._auth_layout(pygame, screen.get_size())
                username_input.set_rect(layout["username_rect"])
                password_input.set_rect(layout["password_rect"])
                login_button.set_rect(layout["login_rect"])
                quit_button.set_rect(layout["quit_rect"])

                if inputs[focused_index].focused:
                    pygame.key.set_text_input_rect(inputs[focused_index].rect)

                for text_input in inputs:
                    text_input.update(dt)

                trigger_login = False

                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        return None

                    if event.type == pygame.VIDEORESIZE:
                        screen = pygame.display.set_mode(event.size, pygame.RESIZABLE)
                        pygame.key.set_text_input_rect(inputs[focused_index].rect)
                        continue

                    if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                        return None

                    if event.type == pygame.KEYDOWN and event.key == pygame.K_TAB:
                        direction = -1 if event.mod & pygame.KMOD_SHIFT else 1
                        inputs[focused_index].focused = False
                        focused_index = (focused_index + direction) % len(inputs)
                        inputs[focused_index].focused = True
                        inputs[focused_index].clear_cursor()
                        pygame.key.set_text_input_rect(inputs[focused_index].rect)
                        continue

                    if login_button.handle_event(event):
                        trigger_login = True
                    if quit_button.handle_event(event):
                        return None

                    for idx, text_input in enumerate(inputs):
                        submitted = text_input.handle_event(event)
                        if text_input.focused and focused_index != idx:
                            inputs[focused_index].focused = False
                            focused_index = idx
                            pygame.key.set_text_input_rect(inputs[focused_index].rect)
                        if submitted:
                            if idx < len(inputs) - 1:
                                inputs[focused_index].focused = False
                                focused_index = idx + 1
                                inputs[focused_index].focused = True
                                inputs[focused_index].clear_cursor()
                                pygame.key.set_text_input_rect(inputs[focused_index].rect)
                            else:
                                trigger_login = True

                if trigger_login and not login_task:
                    username = username_input.text.strip()
                    password = password_input.text.strip()
                    login_task = asyncio.create_task(self._attempt_login(username, password))
                    status_message = "Signing in..."
                    status_color = self.TEXT_COLOR

                if login_task and login_task.done():
                    try:
                        session = login_task.result()
                    except ValueError as exc:
                        status_message = str(exc)
                        status_color = self.ERROR_COLOR
                        login_task = None
                    except httpx.HTTPStatusError as exc:
                        if exc.response is not None and exc.response.status_code == 401:
                            status_message = "Login failed: check your username and password."
                        else:
                            code = exc.response.status_code if exc.response else "?"
                            status_message = f"Login failed: {code}"
                        status_color = self.ERROR_COLOR
                        login_task = None
                    except httpx.HTTPError as exc:
                        status_message = f"Network error: {exc.__class__.__name__}"
                        status_color = self.ERROR_COLOR
                        login_task = None
                    else:
                        return session

                fonts = self._resolve_fonts(pygame, screen.get_size())
                self._draw_liquid_background(pygame, screen, background_phase)

                panel_rect = layout["panel_rect"]
                self._draw_glass_panel(pygame, screen, panel_rect)

                title = fonts["title"].render("Welcome back", True, (240, 244, 255))
                screen.blit(title, (panel_rect.x + 36, panel_rect.y + 30))

                wrap_lines(
                    screen,
                    fonts["body"],
                    "Sign in with the credentials you created on the web dashboard to jump into the latest worlds.",
                    pygame.Rect(panel_rect.x + 36, panel_rect.y + 80, panel_rect.width - 72, 80),
                    color=(210, 218, 255),
                )

                username_input.draw(screen, fonts["input"], fonts["label"])
                password_input.draw(screen, fonts["input"], fonts["label"])
                login_button.draw(screen, fonts["button"])
                quit_button.draw(screen, fonts["button"])

                hint_surface = fonts["hint"].render(
                    "Need an account? Visit the Nigh.ty dashboard to create one.", True, (190, 200, 240)
                )
                hint_rect = hint_surface.get_rect()
                hint_rect.midbottom = (panel_rect.centerx, panel_rect.bottom - 16)
                screen.blit(hint_surface, hint_rect)

                if status_message:
                    status_surface = fonts["body"].render(status_message, True, status_color)
                    screen.blit(status_surface, (panel_rect.x + 36, panel_rect.bottom - 80))

                pygame.display.flip()
                await asyncio.sleep(0)
        finally:
            pygame.key.stop_text_input()

    def _resolve_fonts(self, pygame, size: tuple[int, int]) -> dict[str, "pygame.font.Font"]:
        width, height = size
        scale = max(width / 960, height / 640)
        base = max(18, int(18 * scale))
        title = max(32, int(34 * scale))
        input_size = max(22, int(24 * scale))
        button_size = max(20, int(22 * scale))
        label_size = max(14, int(15 * scale))
        hint_size = max(16, int(17 * scale))

        def font(size: int, bold: bool = False):
            return pygame.font.SysFont("sfprodisplay", size, bold=bold)

        return {
            "title": font(title, bold=True),
            "body": font(base),
            "input": font(input_size),
            "label": font(label_size),
            "button": font(button_size, bold=True),
            "hint": font(hint_size),
        }

    def _draw_liquid_background(self, pygame, screen, phase: float) -> None:
        width, height = screen.get_size()
        gradient = pygame.Surface((width, height))
        top = pygame.Color(16, 22, 48)
        bottom = pygame.Color(6, 12, 28)
        for y in range(height):
            blend = y / max(1, height - 1)
            color = pygame.Color(
                int(top.r * (1 - blend) + bottom.r * blend),
                int(top.g * (1 - blend) + bottom.g * blend),
                int(top.b * (1 - blend) + bottom.b * blend),
            )
            pygame.draw.line(gradient, color, (0, y), (width, y))
        screen.blit(gradient, (0, 0))

        ripple = pygame.Surface((width, height), pygame.SRCALPHA)
        for index in range(3):
            radius = int((width + height) * (0.18 + index * 0.12))
            offset_x = int(width * 0.5 + math.sin(phase * (0.9 + index * 0.1)) * width * 0.25)
            offset_y = int(height * 0.5 + math.cos(phase * (0.7 + index * 0.05)) * height * 0.2)
            color = pygame.Color(80 + index * 30, 130 + index * 40, 255, 55 - index * 10)
            pygame.draw.circle(ripple, color, (offset_x, offset_y), radius)

        blur = pygame.transform.smoothscale(pygame.transform.smoothscale(ripple, (width // 2, height // 2)), (width, height))
        screen.blit(blur, (0, 0), special_flags=pygame.BLEND_ADD)

    def _draw_glass_panel(self, pygame, screen, rect) -> None:
        panel = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
        pygame.draw.rect(panel, (255, 255, 255, 70), panel.get_rect(), border_radius=28)
        highlight = pygame.Surface((rect.width, rect.height // 2), pygame.SRCALPHA)
        pygame.draw.ellipse(highlight, (255, 255, 255, 80), highlight.get_rect())
        highlight = pygame.transform.smoothscale(highlight, (rect.width, rect.height // 2))
        panel.blit(highlight, (0, 0), special_flags=pygame.BLEND_ADD)
        pygame.draw.rect(panel, (255, 255, 255, 160), panel.get_rect(), width=2, border_radius=28)
        screen.blit(panel, rect)

    def _auth_layout(self, pygame, size: tuple[int, int]) -> dict[str, "pygame.Rect"]:
        width, height = size
        panel_width = max(420, min(int(width * 0.7), 640))
        panel_height = max(420, min(int(height * 0.78), 540))
        panel_rect = pygame.Rect(
            (width - panel_width) // 2,
            (height - panel_height) // 2,
            panel_width,
            panel_height,
        )
        input_width = panel_width - 72
        username_rect = pygame.Rect(panel_rect.x + 36, panel_rect.y + 160, input_width, 60)
        password_rect = pygame.Rect(panel_rect.x + 36, username_rect.bottom + 28, input_width, 60)

        button_width = (panel_width - 96) // 2
        login_rect = pygame.Rect(panel_rect.x + 36, panel_rect.bottom - 116, button_width, 58)
        quit_rect = pygame.Rect(panel_rect.right - button_width - 36, panel_rect.bottom - 116, button_width, 58)

        return {
            "panel_rect": panel_rect,
            "username_rect": username_rect,
            "password_rect": password_rect,
            "login_rect": login_rect,
            "quit_rect": quit_rect,
        }

    def _menu_layout(self, pygame, size: tuple[int, int]) -> dict[str, "pygame.Rect"]:
        width, height = size
        panel_width = max(640, min(int(width * 0.85), 1080))
        panel_height = max(520, min(int(height * 0.88), 760))
        panel_rect = pygame.Rect(
            (width - panel_width) // 2,
            (height - panel_height) // 2,
            panel_width,
            panel_height,
        )

        side_width = max(240, int(panel_width * 0.28))
        list_width = panel_width - side_width - 72
        list_rect = pygame.Rect(panel_rect.x + 36, panel_rect.y + 150, list_width, panel_height - 220)

        actions_x = list_rect.right + 24
        actions_height = list_rect.height
        actions_rect = pygame.Rect(actions_x, list_rect.y, side_width, actions_height)
        info_height = min(160, max(120, int(actions_height * 0.38)))
        info_rect = pygame.Rect(actions_rect.x, panel_rect.y + 150, actions_rect.width, info_height)

        button_height = 56
        button_spacing = 14
        max_buttons_height = button_height * 3 + button_spacing * 2
        button_start = info_rect.bottom + 16
        if button_start + max_buttons_height > actions_rect.bottom - 12:
            button_start = actions_rect.bottom - max_buttons_height - 12
            button_start = max(button_start, info_rect.bottom + 8)
        refresh_rect = pygame.Rect(actions_rect.x, button_start, actions_rect.width, button_height)
        join_rect = pygame.Rect(actions_rect.x, refresh_rect.bottom + button_spacing, actions_rect.width, button_height)
        back_rect = pygame.Rect(actions_rect.x, join_rect.bottom + button_spacing, actions_rect.width, button_height)

        create_width = max(150, min(220, panel_width - 240))
        new_world_rect = pygame.Rect(panel_rect.x + 36, panel_rect.bottom - 100, list_rect.width - create_width - 16, 60)
        create_rect = pygame.Rect(new_world_rect.right + 16, new_world_rect.y, create_width, 60)
        status_rect = pygame.Rect(panel_rect.x + 36, panel_rect.bottom - 150, panel_rect.width - 72, 28)

        return {
            "panel_rect": panel_rect,
            "list_rect": list_rect,
            "actions_rect": actions_rect,
            "info_rect": info_rect,
            "refresh_rect": refresh_rect,
            "join_rect": join_rect,
            "back_rect": back_rect,
            "new_world_rect": new_world_rect,
            "create_rect": create_rect,
            "status_rect": status_rect,
        }

    def _visible_world_slots(self, list_rect) -> int:
        return max(1, (list_rect.height - 32) // self.LIST_ITEM_HEIGHT)

    def _draw_world_details(self, pygame, screen, fonts, rect, world: Optional[dict]) -> None:
        details = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
        pygame.draw.rect(details, (255, 255, 255, 60), details.get_rect(), border_radius=22)
        pygame.draw.rect(details, (255, 255, 255, 120), details.get_rect(), width=2, border_radius=22)
        screen.blit(details, rect)

        if not world:
            placeholder = fonts["body"].render("Select a world to see the details", True, (195, 205, 240))
            screen.blit(placeholder, placeholder.get_rect(center=rect.center))
            return

        name_surface = fonts["body"].render(world["name"], True, (25, 32, 70))
        screen.blit(name_surface, (rect.x + 20, rect.y + 18))

        subtitle = fonts["hint"].render(f"ID • {world['id']}", True, (80, 100, 160))
        screen.blit(subtitle, (rect.x + 20, rect.y + 54))

        players = world.get("players", 0)
        details_surface = fonts["hint"].render(f"Players online • {players}", True, (80, 100, 160))
        screen.blit(details_surface, (rect.x + 20, rect.y + 84))

    async def _menu_screen(self, pygame_module, screen, clock, session: AuthSession) -> Optional[str]:
        pygame = pygame_module
        background_phase = 0.0

        layout = self._menu_layout(pygame, screen.get_size())
        fonts = self._resolve_fonts(pygame, screen.get_size())

        new_world_input = TextInput(layout["new_world_rect"], "World name", max_length=48)
        create_button = Button(layout["create_rect"], "Create world")
        refresh_button = Button(layout["refresh_rect"], "Refresh")
        join_button = Button(layout["join_rect"], "Join world")
        logout_button = Button(layout["back_rect"], "Sign out")

        pygame.key.start_text_input()

        worlds: list[dict] = []
        selected_index: Optional[int] = None
        scroll_index = 0
        status_message: Optional[str] = "Loading worlds..."
        status_color = self.TEXT_COLOR

        list_task: Optional[asyncio.Task[list[dict]]] = asyncio.create_task(self._list_worlds(session))
        create_task: Optional[asyncio.Task[dict]] = None
        pending_selection_id: Optional[str] = None

        try:
            while True:
                dt = clock.tick(60) / 1000.0
                background_phase += dt * 0.35

                layout = self._menu_layout(pygame, screen.get_size())
                fonts = self._resolve_fonts(pygame, screen.get_size())

                new_world_input.set_rect(layout["new_world_rect"])
                create_button.set_rect(layout["create_rect"])
                refresh_button.set_rect(layout["refresh_rect"])
                join_button.set_rect(layout["join_rect"])
                logout_button.set_rect(layout["back_rect"])

                if new_world_input.focused:
                    pygame.key.set_text_input_rect(new_world_input.rect)

                new_world_input.update(dt)

                trigger_refresh = False
                trigger_join = False
                trigger_create = False

                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        return None

                    if event.type == pygame.VIDEORESIZE:
                        screen = pygame.display.set_mode(event.size, pygame.RESIZABLE)
                        continue

                    if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                        return None

                    if event.type == pygame.MOUSEWHEEL:
                        visible_slots = self._visible_world_slots(layout["list_rect"])
                        max_offset = max(0, len(worlds) - visible_slots)
                        scroll_index = max(0, min(scroll_index - event.y, max_offset))
                        continue

                    if refresh_button.handle_event(event):
                        trigger_refresh = True
                    if join_button.handle_event(event):
                        trigger_join = True
                    if logout_button.handle_event(event):
                        return None
                    if create_button.handle_event(event):
                        trigger_create = True

                    submitted = new_world_input.handle_event(event)
                    if new_world_input.focused:
                        pygame.key.set_text_input_rect(new_world_input.rect)
                    if submitted:
                        trigger_create = True

                    if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                        candidate = self._world_index_from_position(
                            worlds, layout["list_rect"], event.pos, scroll_index
                        )
                        if candidate is not None:
                            selected_index = candidate

                if trigger_refresh and not (list_task and not list_task.done()):
                    list_task = asyncio.create_task(self._list_worlds(session))
                    status_message = "Refreshing worlds..."
                    status_color = self.TEXT_COLOR

                if trigger_join:
                    if selected_index is not None and 0 <= selected_index < len(worlds):
                        return worlds[selected_index]["id"]
                    status_message = "Select a world to join."
                    status_color = self.ERROR_COLOR

                if trigger_create and not (create_task and not create_task.done()):
                    world_name = new_world_input.text.strip()
                    if not world_name:
                        status_message = "Enter a name for the new world."
                        status_color = self.ERROR_COLOR
                    else:
                        create_task = asyncio.create_task(self._create_world(world_name, session))
                        status_message = "Creating world..."
                        status_color = self.TEXT_COLOR

                if list_task and list_task.done():
                    try:
                        worlds = list_task.result()
                    except httpx.HTTPError as exc:
                        status_message = f"Failed to load worlds: {exc.__class__.__name__}"
                        status_color = self.ERROR_COLOR
                    else:
                        status_message = f"Loaded {len(worlds)} worlds."
                        status_color = self.TEXT_COLOR
                        if pending_selection_id:
                            selected_index = next(
                                (idx for idx, world in enumerate(worlds) if world["id"] == pending_selection_id),
                                None,
                            )
                            pending_selection_id = None
                        if selected_index is None and worlds:
                            selected_index = 0
                        visible_slots = self._visible_world_slots(layout["list_rect"])
                        scroll_index = min(scroll_index, max(0, len(worlds) - visible_slots))
                    finally:
                        list_task = None

                if create_task and create_task.done():
                    try:
                        created = create_task.result()
                    except httpx.HTTPStatusError as exc:
                        status_message = f"World creation failed: {exc.response.status_code}"
                        status_color = self.ERROR_COLOR
                    except httpx.HTTPError as exc:
                        status_message = f"World creation failed: {exc.__class__.__name__}"
                        status_color = self.ERROR_COLOR
                    else:
                        status_message = f"Created world '{created['name']}'."
                        status_color = self.TEXT_COLOR
                        new_world_input.text = ""
                        pending_selection_id = created.get("id")
                        list_task = asyncio.create_task(self._list_worlds(session))
                    finally:
                        create_task = None

                self._draw_liquid_background(pygame, screen, background_phase)
                self._draw_glass_panel(pygame, screen, layout["panel_rect"])
                self._draw_glass_panel(pygame, screen, layout["actions_rect"])

                header = fonts["title"].render(f"Hello, {session.username}", True, (240, 244, 255))
                screen.blit(header, (layout["panel_rect"].x + 36, layout["panel_rect"].y + 36))

                wrap_lines(
                    screen,
                    fonts["body"],
                    "Choose a shared world to drop into or spin up a fresh shard for your friends.",
                    pygame.Rect(layout["panel_rect"].x + 36, layout["panel_rect"].y + 90, layout["panel_rect"].width - 240, 60),
                    color=(210, 218, 255),
                )

                self._draw_world_list(
                    pygame,
                    screen,
                    fonts,
                    layout["list_rect"],
                    worlds,
                    selected_index,
                    scroll_index,
                )

                selected_world = (
                    worlds[selected_index]
                    if selected_index is not None and 0 <= selected_index < len(worlds)
                    else None
                )
                self._draw_world_details(pygame, screen, fonts, layout["info_rect"], selected_world)

                refresh_button.draw(screen, fonts["button"])
                join_button.draw(screen, fonts["button"])
                logout_button.draw(screen, fonts["button"])
                new_world_input.draw(screen, fonts["input"], fonts["label"])
                create_button.draw(screen, fonts["button"])

                if status_message:
                    status_surface = fonts["body"].render(status_message, True, status_color)
                    screen.blit(status_surface, layout["status_rect"].topleft)

                pygame.display.flip()
                await asyncio.sleep(0)
        finally:
            pygame.key.stop_text_input()

    async def _attempt_login(self, username: str, password: str) -> AuthSession:
        if not username or not password:
            raise ValueError("Username and password are required")
        return await self._client.login(username, password)

    async def _list_worlds(self, session: AuthSession) -> list[dict]:
        return await self._client.list_worlds(session.token)

    async def _create_world(self, world_name: str, session: AuthSession) -> dict:
        return await self._client.create_world(world_name, session.token)

    def _draw_world_list(
        self,
        pygame,
        screen,
        fonts,
        list_rect,
        worlds: list[dict],
        selected_index: Optional[int],
        scroll_index: int,
    ) -> None:
        container = pygame.Surface((list_rect.width, list_rect.height), pygame.SRCALPHA)
        pygame.draw.rect(container, (255, 255, 255, 55), container.get_rect(), border_radius=24)
        pygame.draw.rect(container, (255, 255, 255, 120), container.get_rect(), width=2, border_radius=24)

        if not worlds:
            empty = fonts["body"].render("No worlds available yet.", True, (195, 205, 240))
            container.blit(empty, empty.get_rect(center=container.get_rect().center))
            screen.blit(container, list_rect)
            return

        item_height = self.LIST_ITEM_HEIGHT
        visible_slots = self._visible_world_slots(list_rect)
        for row in range(visible_slots):
            idx = scroll_index + row
            if idx >= len(worlds):
                break
            item_rect = pygame.Rect(16, 16 + row * item_height, list_rect.width - 32, item_height - 12)
            is_selected = selected_index == idx
            base_color = pygame.Color(150, 200, 255, 110) if is_selected else pygame.Color(255, 255, 255, 40)
            border_alpha = 200 if is_selected else 110
            pygame.draw.rect(container, base_color, item_rect, border_radius=18)
            pygame.draw.rect(container, (255, 255, 255, border_alpha), item_rect, width=2, border_radius=18)

            world = worlds[idx]
            name_surface = fonts["body"].render(world["name"], True, (20, 26, 60))
            container.blit(name_surface, (item_rect.x + 18, item_rect.y + 8))

            subtitle_text = f"Players online • {world.get('players', 0)}"
            subtitle_surface = fonts["hint"].render(subtitle_text, True, (90, 110, 165))
            container.blit(subtitle_surface, (item_rect.x + 18, item_rect.y + 36))

        if len(worlds) > visible_slots:
            track_height = list_rect.height - 32
            bar_height = max(24, int(track_height * (visible_slots / len(worlds))))
            max_offset = max(1, len(worlds) - visible_slots)
            bar_top = int((track_height - bar_height) * (scroll_index / max_offset))
            scrollbar = pygame.Rect(list_rect.width - 14, 16 + bar_top, 6, bar_height)
            pygame.draw.rect(container, (255, 255, 255, 140), scrollbar, border_radius=3)

        screen.blit(container, list_rect)

    def _world_index_from_position(
        self,
        worlds: list[dict],
        list_rect,
        mouse_pos: tuple[int, int],
        scroll_index: int,
    ) -> Optional[int]:
        import pygame

        if not list_rect.collidepoint(mouse_pos):
            return None
        x, y = mouse_pos
        relative_y = y - (list_rect.y + 16)
        if relative_y < 0:
            return None
        row = int(relative_y // self.LIST_ITEM_HEIGHT)
        candidate = scroll_index + row
        if candidate >= len(worlds):
            return None
        item_rect = pygame.Rect(list_rect.x + 16, list_rect.y + 16 + row * self.LIST_ITEM_HEIGHT, list_rect.width - 32, self.LIST_ITEM_HEIGHT - 12)
        if not item_rect.collidepoint(mouse_pos):
            return None
        return candidate


class TextInput:
    def __init__(
        self,
        rect,
        placeholder: str,
        initial: str = "",
        *,
        masked: bool = False,
        max_length: int = 128,
    ) -> None:
        self.rect = rect
        self.placeholder = placeholder
        self.text = initial
        self.focused = False
        self.masked = masked
        self.max_length = max_length
        self._cursor_timer = 0.0
        self._cursor_visible = True

    def set_rect(self, rect) -> None:
        if self.rect != rect:
            self.rect = rect

    def update(self, dt: float) -> None:
        blink_period = 0.9
        self._cursor_timer += dt
        if self._cursor_timer >= blink_period:
            self._cursor_timer %= blink_period
            self._cursor_visible = not self._cursor_visible

    def clear_cursor(self) -> None:
        self._cursor_timer = 0.0
        self._cursor_visible = True

    def handle_event(self, event) -> bool:
        import pygame

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            was_focused = self.focused
            self.focused = self.rect.collidepoint(event.pos)
            if self.focused and not was_focused:
                self.clear_cursor()
            return False

        if not self.focused:
            return False

        if event.type == pygame.TEXTINPUT:
            if event.text:
                remaining = self.max_length - len(self.text)
                if remaining > 0:
                    self.text += event.text[:remaining]
                    self.clear_cursor()
            return False

        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_RETURN:
                return True
            if event.key == pygame.K_BACKSPACE:
                self.text = self.text[:-1]
                self.clear_cursor()
            elif event.key == pygame.K_ESCAPE:
                self.focused = False
            return False

        return False

    def draw(self, screen, value_font, label_font) -> None:
        import pygame

        base = pygame.Surface((self.rect.width, self.rect.height), pygame.SRCALPHA)
        overlay_alpha = 140 if self.focused else 105
        pygame.draw.rect(base, (255, 255, 255, overlay_alpha), base.get_rect(), border_radius=18)
        border_alpha = 200 if self.focused else 140
        pygame.draw.rect(base, (255, 255, 255, border_alpha), base.get_rect(), width=2, border_radius=18)
        screen.blit(base, self.rect)

        label_surface = label_font.render(self.placeholder, True, (230, 236, 255))
        label_pos = (self.rect.x + 8, self.rect.y - label_surface.get_height() - 6)
        screen.blit(label_surface, label_pos)

        display_text = self.text
        if self.masked and self.text:
            display_text = "●" * len(self.text)

        if display_text:
            text_surface = value_font.render(display_text, True, (25, 30, 60))
        else:
            text_surface = value_font.render("Click to enter", True, (110, 120, 170))

        text_rect = text_surface.get_rect()
        text_rect.midleft = (self.rect.x + 18, self.rect.y + self.rect.height / 2)
        screen.blit(text_surface, text_rect)

        if self.focused and self._cursor_visible:
            caret_x = text_rect.right if display_text else text_rect.left
            caret_rect = pygame.Rect(caret_x + 2, self.rect.y + 12, 2, self.rect.height - 24)
            pygame.draw.rect(screen, (60, 70, 150), caret_rect)


class Button:
    def __init__(self, rect, label: str) -> None:
        self.rect = rect
        self.label = label
        self._pressed = False
        self._hovered = False

    def set_rect(self, rect) -> None:
        if self.rect != rect:
            self.rect = rect

    def handle_event(self, event) -> bool:
        import pygame

        if event.type == pygame.MOUSEMOTION:
            self._hovered = self.rect.collidepoint(event.pos)

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos):
                self._pressed = True
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self._pressed and self.rect.collidepoint(event.pos):
                self._pressed = False
                return True
            self._pressed = False
        return False

    def draw(self, screen, font) -> None:
        import pygame

        self._hovered = self.rect.collidepoint(pygame.mouse.get_pos())

        base = pygame.Surface((self.rect.width, self.rect.height), pygame.SRCALPHA)
        gradient_top = pygame.Color(120, 180, 255, 210)
        gradient_bottom = pygame.Color(80, 130, 240, 230)
        if self._pressed:
            gradient_top, gradient_bottom = gradient_bottom, gradient_top
        elif self._hovered:
            gradient_top = pygame.Color(150, 200, 255, 230)

        for y in range(self.rect.height):
            blend = y / max(1, self.rect.height - 1)
            r = int(gradient_top.r * (1 - blend) + gradient_bottom.r * blend)
            g = int(gradient_top.g * (1 - blend) + gradient_bottom.g * blend)
            b = int(gradient_top.b * (1 - blend) + gradient_bottom.b * blend)
            a = int(gradient_top.a * (1 - blend) + gradient_bottom.a * blend)
            pygame.draw.line(base, (r, g, b, a), (0, y), (self.rect.width, y))

        pygame.draw.rect(base, (255, 255, 255, 80), base.get_rect(), border_radius=18)
        pygame.draw.rect(base, (255, 255, 255, 160), base.get_rect(), width=2, border_radius=18)
        screen.blit(base, self.rect)

        label_surface = font.render(self.label, True, (20, 28, 60))
        screen.blit(label_surface, label_surface.get_rect(center=self.rect.center))


def wrap_lines(screen, font, text: str, bounds, *, color) -> None:
    x, y, width, _height = bounds
    words = text.split()
    line = ""
    for word in words:
        candidate = f"{line} {word}".strip()
        if font.size(candidate)[0] <= width:
            line = candidate
            continue
        if line:
            surface = font.render(line, True, color)
            screen.blit(surface, (x, y))
            y += surface.get_height() + 2
        line = word

    if line:
        surface = font.render(line, True, color)
        screen.blit(surface, (x, y))


async def async_main(args: argparse.Namespace) -> None:
    async with ServerClient(args.server) as client:
        app = ClientApplication(client, default_username=args.username, default_password=args.password)
        session: Optional[AuthSession] = None
        while True:
            result = await app.run(resume_session=session)
            if result is None or result.requested_world is None:
                return

            session = result.session
            config = await client.get_config()
            ws_url = args.ws or http_to_ws(args.server)
            game = GameClient(
                ws_url,
                result.requested_world,
                session.token,
                session.username,
                initial_config=config,
            )
            await game.run()

            # Returning to the menu allows the player to pick another world or exit.
            if not game.was_eliminated():
                # If the player closed the game window intentionally, keep the existing session
                # and present the lobby again so they can choose what to do next.
                continue


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    asyncio.run(async_main(args))


if __name__ == "__main__":  # pragma: no cover
    main()
