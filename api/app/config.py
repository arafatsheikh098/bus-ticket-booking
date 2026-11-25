from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://aashon:aashon@localhost:5433/aashon"

    # How long a seat hold lives before it is released automatically.
    hold_ttl_seconds: int = 300
    # Extra time granted once, when the passenger moves on to payment.
    payment_grace_seconds: int = 120
    max_seats_per_hold: int = 4

    fakepay_url: str = "http://localhost:8001"
    fakepay_api_key: str = "sk_test_dev_change_me"
    fakepay_webhook_secret: str = "whsec_dev_change_me"
    # Base URL the payment provider uses to reach our webhook endpoint.
    callback_base_url: str = "http://localhost:8000"
    # Where the browser returns after checkout.
    web_url: str = "http://localhost:5173"

    sweeper_interval_seconds: float = 2.0
    run_background_tasks: bool = True
    seed_demo_data: bool = True


settings = Settings()
