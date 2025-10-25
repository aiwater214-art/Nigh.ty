"""Graphical launcher and entry point for the multiplayer client."""
from __future__ import annotations

import argparse
import asyncio
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
    parser.add_argument("--username", default=os.getenv("PLAYER_NAME", ""), help="Default player display name shown on the login screen")
    parser.add_argument("--dashboard-token", default=os.getenv("DASHBOARD_TOKEN", ""), help="Default dashboard authentication token shown on the login screen")
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

    def __init__(self, client: ServerClient, *, default_username: str = "", default_token: str = "") -> None:
        self._client = client
        self._default_username = default_username
        self._default_token = default_token

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
        font = pygame.font.SysFont("arial", 24)
        small_font = pygame.font.SysFont("arial", 18)

        pygame.key.start_text_input()
        try:
            username_input = TextInput(pygame.Rect(300, 250, 360, 40), "Username", self._default_username, max_length=32)
            token_input = TextInput(
                pygame.Rect(300, 310, 360, 40), "Dashboard token", self._default_token, masked=True, max_length=128
            )
            inputs = [username_input, token_input]
            focused_index = 0
            inputs[focused_index].focused = True

            login_button = Button(pygame.Rect(300, 370, 160, 44), "Sign In")
            quit_button = Button(pygame.Rect(500, 370, 160, 44), "Quit")

            status_message: Optional[str] = None
            status_color = self.TEXT_COLOR
            login_task: Optional[asyncio.Task[AuthSession]] = None

            while True:
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        return None

                    if event.type == pygame.KEYDOWN and event.key == pygame.K_TAB:
                        inputs[focused_index].focused = False
                        focused_index = (focused_index + 1) % len(inputs)
                        inputs[focused_index].focused = True
                        continue

                    submitted = inputs[focused_index].handle_event(event)
                    if submitted and focused_index == len(inputs) - 1:
                        if not login_task:
                            login_task = asyncio.create_task(
                                self._attempt_login(username_input.text.strip(), token_input.text.strip())
                            )
                            status_message = "Signing in..."
                            status_color = self.TEXT_COLOR

                    login_clicked = login_button.handle_event(event)
                    quit_clicked = quit_button.handle_event(event)

                    if login_clicked and not login_task:
                        login_task = asyncio.create_task(
                            self._attempt_login(username_input.text.strip(), token_input.text.strip())
                        )
                        status_message = "Signing in..."
                        status_color = self.TEXT_COLOR

                    if quit_clicked:
                        return None

                if login_task and login_task.done():
                    try:
                        session = login_task.result()
                    except ValueError as exc:
                        status_message = str(exc)
                        status_color = self.ERROR_COLOR
                        login_task = None
                    except httpx.HTTPStatusError as exc:
                        if exc.response is not None and exc.response.status_code == 401:
                            status_message = "Login failed: check your username and dashboard token."
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

                screen.fill(self.BG_COLOR)
                pygame.draw.rect(screen, self.PANEL_COLOR, pygame.Rect(180, 120, 600, 360), border_radius=12)

                title = font.render("Sign in to play", True, self.TEXT_COLOR)
                screen.blit(title, title.get_rect(center=(self.WINDOW_SIZE[0] // 2, 170)))

                wrap_lines(
                    screen,
                    small_font,
                    "Create an account on the Nigh.ty web dashboard and paste your dashboard token here to continue.",
                    pygame.Rect(220, 200, 520, 40),
                    color=self.TEXT_COLOR,
                )

                username_input.draw(screen, font)
                token_input.draw(screen, font)
                login_button.draw(screen, font)
                quit_button.draw(screen, font)

                if status_message:
                    status_surface = small_font.render(status_message, True, status_color)
                    screen.blit(status_surface, status_surface.get_rect(center=(self.WINDOW_SIZE[0] // 2, 430)))

                pygame.display.flip()
                clock.tick(60)
                await asyncio.sleep(0)
        finally:
            pygame.key.stop_text_input()

    async def _menu_screen(self, pygame_module, screen, clock, session: AuthSession) -> Optional[str]:
        pygame = pygame_module
        font = pygame.font.SysFont("arial", 24)
        small_font = pygame.font.SysFont("arial", 18)

        pygame.key.start_text_input()
        try:
            new_world_input = TextInput(pygame.Rect(220, 470, 280, 40), "New world name", max_length=48)
            create_button = Button(pygame.Rect(520, 470, 140, 40), "Create world")
            refresh_button = Button(pygame.Rect(680, 200, 140, 36), "Refresh")
            join_button = Button(pygame.Rect(680, 250, 140, 36), "Join world")
            logout_button = Button(pygame.Rect(680, 300, 140, 36), "Back")

            worlds: list[dict] = []
            selected_index: Optional[int] = None
            status_message: Optional[str] = "Loading worlds..."
            status_color = self.TEXT_COLOR

            list_task: Optional[asyncio.Task[list[dict]]] = asyncio.create_task(self._list_worlds(session))
            create_task: Optional[asyncio.Task[dict]] = None
            pending_selection_id: Optional[str] = None

            while True:
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        return None

                    new_world_input.handle_event(event)
                    if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                        selected_index = self._handle_world_click(worlds, event.pos)

                    if refresh_button.handle_event(event) and not (list_task and not list_task.done()):
                        list_task = asyncio.create_task(self._list_worlds(session))
                        status_message = "Refreshing worlds..."
                        status_color = self.TEXT_COLOR

                    if join_button.handle_event(event):
                        if selected_index is not None and 0 <= selected_index < len(worlds):
                            return worlds[selected_index]["id"]
                        status_message = "Select a world to join."
                        status_color = self.ERROR_COLOR

                    if logout_button.handle_event(event):
                        return None

                    if create_button.handle_event(event) and not (create_task and not create_task.done()):
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
                        if selected_index is None:
                            selected_index = 0 if worlds else None
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

                screen.fill(self.BG_COLOR)
                pygame.draw.rect(screen, self.PANEL_COLOR, pygame.Rect(160, 120, 640, 400), border_radius=12)

                header = font.render(f"Welcome, {session.username}", True, self.TEXT_COLOR)
                screen.blit(header, (180, 140))

                wrap_lines(
                    screen,
                    small_font,
                    "Select an existing world to join or create a new one. Worlds are shared across all players connected to the server.",
                    pygame.Rect(180, 170, 460, 60),
                    color=self.TEXT_COLOR,
                )

                self._draw_world_list(pygame, screen, font, small_font, worlds, selected_index)

                refresh_button.draw(screen, small_font)
                join_button.draw(screen, small_font)
                logout_button.draw(screen, small_font)

                new_world_input.draw(screen, font)
                create_button.draw(screen, small_font)

                if status_message:
                    status_surface = small_font.render(status_message, True, status_color)
                    screen.blit(status_surface, (180, 530))

                pygame.display.flip()
                clock.tick(60)
                await asyncio.sleep(0)
        finally:
            pygame.key.stop_text_input()

    async def _attempt_login(self, username: str, token: str) -> AuthSession:
        if not username or not token:
            raise ValueError("Username and dashboard token are required")
        return await self._client.login(username, token)

    async def _list_worlds(self, session: AuthSession) -> list[dict]:
        return await self._client.list_worlds(session.token)

    async def _create_world(self, world_name: str, session: AuthSession) -> dict:
        return await self._client.create_world(world_name, session.token)

    def _draw_world_list(self, pygame, screen, font, small_font, worlds: list[dict], selected_index: Optional[int]) -> None:
        list_rect = pygame.Rect(200, 230, 440, 220)
        pygame.draw.rect(screen, (15, 18, 40), list_rect, border_radius=8)

        if not worlds:
            empty = small_font.render("No worlds available yet.", True, self.TEXT_COLOR)
            screen.blit(empty, empty.get_rect(center=list_rect.center))
            return

        item_height = 48
        for index, world in enumerate(worlds):
            item_rect = pygame.Rect(list_rect.x + 8, list_rect.y + 8 + index * item_height, list_rect.width - 16, item_height - 12)
            if item_rect.bottom > list_rect.bottom:
                break
            is_selected = selected_index == index
            pygame.draw.rect(screen, self.ACCENT_COLOR if is_selected else (40, 48, 90), item_rect, border_radius=6, width=0 if is_selected else 2)

            name_surface = font.render(world["name"], True, self.TEXT_COLOR if is_selected else (200, 205, 235))
            screen.blit(name_surface, (item_rect.x + 12, item_rect.y + 6))

            subtitle = f"ID: {world['id']}  •  Players: {world.get('players', 0)}"
            subtitle_surface = small_font.render(subtitle, True, (190, 195, 220))
            screen.blit(subtitle_surface, (item_rect.x + 12, item_rect.y + 26))

    def _handle_world_click(self, worlds: list[dict], mouse_pos: tuple[int, int]) -> Optional[int]:
        x, y = mouse_pos
        origin_x, origin_y = 208, 238
        width, height = 424, 36
        for index, _world in enumerate(worlds):
            item_top = origin_y + index * 48
            if origin_y <= y <= origin_y + 220 and origin_x <= x <= origin_x + width:
                if item_top <= y <= item_top + height:
                    return index
        return None


class TextInput:
    def __init__(self, rect, placeholder: str, initial: str = "", *, masked: bool = False, max_length: int = 128) -> None:
        self.rect = rect
        self.placeholder = placeholder
        self.text = initial
        self.focused = False
        self.masked = masked
        self.max_length = max_length

    def handle_event(self, event) -> bool:
        import pygame

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self.focused = self.rect.collidepoint(event.pos)
            return False

        if self.focused and event.type == pygame.TEXTINPUT:
            if event.text:
                remaining = self.max_length - len(self.text)
                if remaining > 0:
                    self.text += event.text[:remaining]
            return False

        if self.focused and event.type == pygame.KEYDOWN:
            if event.key == pygame.K_RETURN:
                return True
            if event.key == pygame.K_BACKSPACE:
                self.text = self.text[:-1]
            elif event.key == pygame.K_ESCAPE:
                self.focused = False
            else:
                if event.unicode and event.unicode.isprintable():
                    if len(self.text) < self.max_length:
                        self.text += event.unicode
        return False

    def draw(self, screen, font) -> None:
        import pygame

        color = (70, 90, 140) if self.focused else (50, 60, 110)
        pygame.draw.rect(screen, color, self.rect, border_radius=8)
        pygame.draw.rect(screen, (160, 180, 220), self.rect, width=2, border_radius=8)

        display_text = self.text
        if self.masked and self.text:
            display_text = "●" * len(self.text)

        if display_text:
            text_surface = font.render(display_text, True, ClientApplication.TEXT_COLOR)
        else:
            text_surface = font.render(self.placeholder, True, (150, 160, 200))
        screen.blit(text_surface, (self.rect.x + 12, self.rect.y + 8))


class Button:
    def __init__(self, rect, label: str) -> None:
        self.rect = rect
        self.label = label
        self._pressed = False

    def handle_event(self, event) -> bool:
        import pygame

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

        color = ClientApplication.ACCENT_COLOR if self._pressed else (70, 90, 180)
        pygame.draw.rect(screen, color, self.rect, border_radius=8)
        pygame.draw.rect(screen, (20, 25, 60), self.rect, width=2, border_radius=8)
        label_surface = font.render(self.label, True, (15, 18, 40))
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
        app = ClientApplication(client, default_username=args.username, default_token=args.dashboard_token)
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
