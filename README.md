# Financial Reporter Project

This project automates the creation of a daily financial report using **Kiwoom Open API** (for asset data) and **Google News RSS** (for economic news), publishing the result to a **Notion Database**.

## Project Structure

```text
financial_reporter/
├── config.yaml          # [IMPORTANT] Configuration file for API Keys
├── main.py              # Main entry point script
├── requirements.txt     # Python dependencies
└── src/
    ├── kiwoom.py        # Kiwoom Open API Wrapper (Mock Mode supported)
    ├── notion.py        # Notion API Client
    └── news.py          # News Fetcher (Google News RSS)
```

## Setup & Usage

### 1. Prerequisites
- **Windows OS**: Required for Kiwoom Open API.
- **Python 32-bit**: Required for `pykiwoom`/`OCX` interaction.
    - Recommended: Python 3.8 ~ 3.11 (32-bit version).
- **Kiwoom Open API+**: Must be installed and running (or capable of auto-login).

### 2. Installation

1.  Create a virtual environment (optional but recommended):
    ```bash
    python -m venv venv
    venv\Scripts\activate
    ```
2.  Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```

### 3. Configuration (`config.yaml`)

You **must** edit `config.yaml` before running the program.

-   **Kiwoom**:
    -   `mock`: Set to `true` to test without connecting to Kiwoom (uses fake data). Set to `false` for real trading.
-   **Notion**:
    -   `token`: Your Notion Integration Internal Integration Token.
    -   `database_id`: The ID of the database where reports will be added.
    -   *Note*: Ensure your Integration is invited to the specific Notion Database page.
-   **News**:
    -   `keywords`: Add any stocks or economic terms you want to track.

### 4. Running

```bash
python main.py
```

## extending the functionalities

### Customizing Kiwoom Data (`src/kiwoom.py`)
Currently, `get_assets()` returns a simplified dict. To fetch real stock holdings:
1.  Uncomment the TR request lines (e.g., `opw00018`).
2.  Implement the parsing logic to convert the DataFrame return into the dictionary format expected by `main.py`.

### Customizing the Report (`src/notion.py`)
Modify `create_report()` to change the layout. You can add more blocks like `table`, `image`, or `quote` by reviewing the [Notion API Documentation](https://developers.notion.com/reference/block).
