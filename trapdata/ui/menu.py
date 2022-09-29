import pathlib
import threading
from functools import partial

import kivy
from kivy.app import App
from kivy.clock import Clock
from kivy.uix.label import Label
from kivy.uix.image import AsyncImage
from kivy.uix.button import Button
from kivy.uix.popup import Popup
from kivy.uix.widget import Widget
from kivy.uix.gridlayout import GridLayout
from kivy.lang import Builder
from kivy.properties import (
    StringProperty,
    ObjectProperty,
    BooleanProperty,
)
from kivy.uix.screenmanager import Screen
from plyer import filechooser

from trapdata import logger
from trapdata import db
from trapdata import models
from trapdata.common import settings
from trapdata.models.queue import add_monitoring_session_to_queue
from trapdata.models.events import (
    get_monitoring_sessions_from_db,
    get_monitoring_sessions_from_filesystem,
    save_monitoring_sessions,
)


kivy.require("2.1.0")

# detect_and_classify = lambda *args, **kwargs: None


Builder.load_file(str(pathlib.Path(__file__).parent / "menu.kv"))


def choose_directory(cache=True, setting_key="last_root_directory", starting_path=None):
    """
    Prompt the user to select a directory where trap data has been saved.
    The subfolders of this directory should be timestamped directories
    with nightly trap images.

    The user's selection is saved and reused on the subsequent launch.
    """
    # @TODO Look for SDCARD / USB Devices first?

    if cache:
        selected_dir = settings.read_setting(setting_key)
    else:
        selected_dir = None

    if selected_dir:
        selected_dir = pathlib.Path(selected_dir)

        if selected_dir.is_dir():
            return selected_dir
        else:
            settings.delete_setting(setting_key)

    selection = filechooser.choose_dir(
        title="Choose the root directory for your nightly trap data",
        path=starting_path,
    )

    if selection:
        selected_dir = selection[0]
    else:
        return None

    settings.save_setting(setting_key, selected_dir)

    return selected_dir


class TrapSesionData(Widget):
    """
    One night / session of trap data.

    Will keep track of which directories have been processed, their cached results, etc.
    Could be backed by a SQLite database? Or just a folder structure under .cache
    """

    pass


class AddToQueueButton(Button):
    monitoring_session = ObjectProperty()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        Clock.schedule_interval(self.update_status, 1)

    def on_release(self):
        add_monitoring_session_to_queue(self.monitoring_session)

    def update_status(self, *args):
        app = App.get_running_app()
        if app.queue:
            self.text = app.queue.status_str


class LaunchScreenButton(Button):
    monitoring_session = ObjectProperty()
    screenname = StringProperty(allownone=True)
    screenmanager = ObjectProperty(allownone=True)

    def on_release(self):
        self.launch()

    def launch(self):
        """
        Open the specified screen
        """

        if self.screenmanager and self.screenname:
            self.screenmanager.current = self.screenname
            self.screenmanager.get_screen(
                self.screenname
            ).monitoring_session = self.monitoring_session


class DataMenuScreen(Screen):
    root_dir = ObjectProperty(allownone=True)
    sessions = ObjectProperty()
    status_popup = ObjectProperty()
    status_clock = ObjectProperty()
    data_ready = BooleanProperty(defaultvalue=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        Clock.schedule_once(self.setup, 1)

    def setup(self, *args):
        if not self.root_dir:
            self.root_dir = choose_directory(cache=True)

    def choose_root_directory(self, *args):
        try:
            self.root_dir = choose_directory(cache=False, starting_path=self.root_dir)
        except Exception as e:
            logger.error(f"Failed to choose directory with a starting path: {e}")
            self.root_dir = choose_directory(cache=False)

    def reload(self):
        """
        Reload the view by changing the root dir.
        """
        root_dir = self.root_dir
        self.root_dir = None
        self.root_dir = root_dir

    def db_ready(self):
        # Try to open a database session. @TODO add GUI indicator and ask to recreate if fails.
        if not db.check_db(self.root_dir):
            Popup(
                title="Error reading database",
                content=Label(
                    text=(
                        f"Error reading database: \n\n"
                        f"{db.db_path(self.root_dir)} \n\n"
                        f"Trying deleting the DB file and it will be recreated on next launch."
                    )
                ),
                size_hint=(None, None),
                size=("550dp", "200dp"),
                # on_dismiss=sys.exit,
            ).open()
            return False
        else:
            return True

    def on_root_dir(self, instance, value):
        root_dir = value
        logger.info("Base directory changed!")

        App.get_running_app().base_path = str(root_dir)

        self.data_ready = False

        if root_dir and self.db_ready():
            label_text = f"Looking for capture data in \n\n{root_dir}"
            self.status_popup = Popup(
                title="Status",
                content=Label(text=label_text),
                size_hint=(None, None),
                size=("550dp", "200dp"),
                auto_dismiss=False,
                on_open=self.get_monitoring_sessions,
            )
            self.status_popup.open()

    def on_data_ready(self, *args):
        if self.data_ready:
            logger.info("Data is ready for other methods")
            # Buttons aren't available immediately
            self.display_monitoring_sessions()
            Clock.schedule_once(self.enable_buttons, 1)
        else:
            self.disable_buttons()

    def enable_buttons(self, *args):
        logger.info("Enabling all buttons")
        for row in self.ids.monitoring_sessions.children:
            for child in row.children:
                if isinstance(child, Button):
                    child.disabled = False

    def disable_buttons(self, *args):
        logger.info("Disabling all buttons")
        for row in self.ids.monitoring_sessions.children:
            for child in row.children:
                if isinstance(child, Button):
                    child.disabled = True

    def get_monitoring_sessions(self, *args):
        self.sessions = get_monitoring_sessions_from_db(self.root_dir)
        if self.sessions:
            self.data_ready = True
        else:
            self.sessions = get_monitoring_sessions_from_filesystem(self.root_dir)
            # @TODO just wait for the DB to save, don't worry about background task
            # Rescan will trigger a scan an resave.
            self.save_monitoring_sessions_in_background()
        self.status_popup.dismiss()

    def save_monitoring_sessions_in_background(self):
        logger.info("Writing monitoring data to DB in the background")
        bgtask = ThreadWithStatus(
            target=partial(save_monitoring_sessions, self.root_dir, self.sessions),
            daemon=True,
            name="writing_monitoring_sessions_to_db",
        )
        bgtask.start()
        self.status_clock = Clock.schedule_interval(
            partial(self.watch_db_progress, bgtask), 1
        )

    def watch_db_progress(self, bgtask, *args):
        logger.debug(f"Checking DB write status: {bgtask}")
        self.ids.status.text = "Writing capture data to the database..."
        if bgtask and not bgtask.is_alive():
            logger.debug(f"Thread has exited: {bgtask}")
            Clock.unschedule(self.status_clock)
            if bgtask.exception:
                self.ids.status.text = "Failed to write capture data to the database"
            else:
                self.sessions = get_monitoring_sessions_from_db(self.root_dir)
                self.data_ready = True
                self.ids.status.text = "Ready"

    def display_monitoring_sessions(self, *args):
        grid = self.ids.monitoring_sessions
        grid.clear_widgets()

        for ms in self.sessions:

            label = (
                f"{ms.day.strftime('%a, %b %e')} \n"
                f"{ms.num_images or 0} images\n"
                f"{ms.duration_label}\n"
                f"{ms.num_detected_objects} objects\n"
            )

            with db.get_session(self.root_dir) as sess:
                first_image = (
                    sess.query(models.Image)
                    .filter_by(monitoring_session_id=ms.id)
                    .first()
                )

            if first_image:
                first_image_path = pathlib.Path(first_image.path)
                bg_image = str(self.root_dir / first_image_path)
            else:
                continue

            # Check if there are unprocessed images in monitoring session?
            btn_disabled = True

            playback_btn = LaunchScreenButton(
                text="Playback",
                monitoring_session=ms,
                screenmanager=self.manager,
                screenname="playback",
                disabled=btn_disabled,
            )

            add_to_queue_btn = AddToQueueButton(
                text="Add to Queue",
                monitoring_session=ms,
                disabled=btn_disabled,
            )

            summary_btn = LaunchScreenButton(
                text="Summary",
                monitoring_session=ms,
                screenmanager=self.manager,
                screenname="summary",
                disabled=btn_disabled,
            )

            row = GridLayout(rows=1, cols=5, spacing=20)
            row.add_widget(AsyncImage(source=bg_image))
            row.add_widget(Label(text=label))
            row.add_widget(playback_btn)
            row.add_widget(add_to_queue_btn)
            row.add_widget(summary_btn)
            grid.add_widget(row)

        self.ids.status.text = "Ready"

    def open_settings(self):
        self.manager.current = "settings"