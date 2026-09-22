from pydantic_settings import BaseSettings

REQUIRED_CHANNELS = {
    "4818711476": "https://ble.ir/kiteck_tm",
    "5171266720": "https://ble.ir/lingrowth",
}


class Settings(BaseSettings):
    BOT_TOKEN: str
    DATABASE_URL: str
    POSTGRES_USER: str = "fastdic"
    POSTGRES_PASSWORD: str = "fastdic"
    POSTGRES_DB: str = "fastdic_bot"
    FOOTER: str = "@Kiteck_TM"
    ADMIN_IDS: str = ""

    model_config = {"env_file": ".env"}


settings = Settings()
