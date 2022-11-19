from typing import Any
import inquirer


class DummyMediaController:
    def set_volume(self, volume: int) -> None:
        pass

    def play_audio(self, stream_url: str) -> None:
        pass

    def turn_off(self) -> None:
        pass

    def get_questions(self) -> list[inquirer.questions.Question]:
        return []

    def process_answers(self, answers: dict[str, Any]):
        pass
