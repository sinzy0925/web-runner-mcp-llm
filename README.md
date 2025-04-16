# Web-Runner-mcp: Advanced Web Browser Operation Protocol for AI

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/)
<!-- Add/modify badges as needed -->

**Web-Runner-mcp** is a Python project designed to make Playwright's powerful browser automation capabilities easily accessible to AI agents and other applications through the standardized **Model Context Protocol (MCP)**.

![Web-Runner Logo](./Web-Runner.png)


## Table of Contents

*   [Overview](#overview)
*   [Why Web-Runner-mcp?](#why-web-runner-mcp)
*   [Key Features](#key-features)
    *   [Supported Actions](#supported-actions)
    *   [PDF Text Extraction](#pdf-text-extraction)
    *   [Error Handling](#error-handling)
*   [Usage](#usage)
    *   [1. Setup](#1-setup)
    *   [2. Starting the Server (SSE Mode Example)](#2-starting-the-server-sse-mode-example)
    *   [3. Creating JSON Data for Web-Runner](#3-creating-json-data-for-web-runner)
        *   [Step 1: Prepare the JSON Generator](#step-1-prepare-the-json-generator)
        *   [Step 2: Get CSS Selectors for Target Elements](#step-2-get-css-selectors-for-target-elements)
        *   [Step 3: Create Operation Steps in json_generator.html](#step-3-create-operation-steps-in-json_generatorhtml)
        *   [Step 4: Place the JSON File](#step-4-place-the-json-file)
    *   [4. Command-Line Execution (for Testing)](#4-command-line-execution-for-testing)
    *   [5. Running from the GUI Client](#5-running-from-the-gui-client)
    *   [6. Usage from AI Applications](#6-usage-from-ai-applications)
*   [JSON Format (Reference)](#json-format-reference)
*   [Comparison with Other Tools](#comparison-with-other-tools)
*   [Future Plans](#future-plans)
*   [Contributing](#contributing)
*   [License](#license)

---

## Overview

Information gathering and interaction with the web are essential for today's AI agents, but existing tools have limitations. While simple content retrieval or fetching search result lists is possible, tasks like interacting with login-required sites, handling pages rendered with complex JavaScript, navigating iframe structures, and processing PDF content remain challenging. Furthermore, reliably controlling low-level APIs like Playwright directly from Large Language Models (LLMs) presents a significant hurdle.

Web-Runner-mcp proposes a new approach to tackle these challenges.

Instead of instructing the LLM to perform individual browser operations, Web-Runner-mcp allows you to define a sequence of desired operations in a JSON format and pass it to an MCP server for execution. The current version executes these operations reliably based on the JSON file instructions, without direct LLM involvement in the browser control loop itself.

This might be a **"small revolution"** in how AI interacts with the web, opening doors to the deeper, more complex parts of the web that were previously inaccessible to AI.

## Why Web-Runner-mcp?

*   **Advanced Web Operations:**
    *   **Login:** Access and interact with websites requiring authentication.
    *   **PDF:** Download linked PDFs and extract their text content.
    *   **Iframe:** Explore and interact with elements within nested iframes (dynamic discovery).
    *   **Multiple Tabs/Pages:** Follow new pages opened by clicks.
    *   **Dynamic Content:** Wait for and interact with elements generated by JavaScript.
*   **Versatile Data Extraction:**
    *   Flexible text/HTML retrieval using `innerText`, `textContent`, `innerHTML`.
    *   Get specific attribute values using `getAttribute`.
    *   Efficient data collection from multiple elements using `getAllAttributes`, `getAllTextContents` (with dynamic iframe discovery).
*   **Declarative Operation Definition:**
    *   Describe the desired steps in JSON.
    *   Ensures reproducibility and simplifies debugging.
*   **MCP Compliance:**
    *   Standardized protocol enables integration with various MCP clients (Dify custom tools, Python AI agent frameworks, custom clients, etc.).
    *   Separates client and server concerns.
*   **Reliable Execution:**
    *   Stable browser operations powered by Playwright.
    *   Appropriate waiting mechanisms and error handling.

## Key Features

*   **MCP Server (`web_runner_mcp_server.py`):** Implemented in Python (based on `FastMCP`), exposes Web-Runner functionality as the `execute_web_runner` tool.
*   **Web-Runner Core (`playwright_handler.py`, `utils.py`, `config.py`):** Uses Playwright (async) to execute browser operations based on input JSON. Handles core logic, settings, utility functions, dynamic iframe discovery, and PDF processing.
*   **Web-Runner Standalone Execution (`main.py`):** An entry point for running Web-Runner directly from the command line without the MCP server (for debugging and unit testing).
*   **MCP Client Core (`web_runner_mcp_client_core.py`):** Provides the core function (`execute_web_runner_via_mcp`) for invoking the MCP server programmatically (e.g., from AI agents).
*   **GUI Client (`web_runner_mcp_client_GUI.py`):** A convenient graphical interface for selecting JSON files, running tasks manually, and launching the JSON generator.

### Supported Actions

*   `click`: Clicks an element.
*   `input`: Enters text into an element.
*   `hover`: Hovers over an element.
*   `get_inner_text`, `get_text_content`, `get_inner_html`: Gets text/HTML (single element).
*   `get_attribute`: Gets an attribute value (single element).
*   `get_all_attributes`, `get_all_text_contents`: Gets attribute values/text content as a list (multiple elements, searches within iframes).
*   `wait_visible`: Waits for an element to become visible.
*   `select_option`: Selects an option from a dropdown list.
*   `screenshot`: Saves a screenshot of the page or an element (server-side).
*   `scroll_page_to_bottom`, `scroll_to_element`: Performs scroll operations.
*   `wait_page_load`: Waits for the page to finish loading.
*   `sleep`: Pauses execution for a specified duration.
*   `switch_to_iframe`, `switch_to_parent_frame`: Moves focus between iframes (explicitly specified).

### PDF Text Extraction

Automatically downloads PDFs linked via `get_attribute(href=...)` or `get_all_attributes(href=...)` and includes the extracted text in the results.

### Error Handling

Records error information for each step, including the screenshot path (on the server's filesystem) if an error occurs.

## Usage

### 1. Setup

**(1) Clone the repository:**

```bash
git clone https://github.com/sinzy0925/web-runner-mcp.git
cd web-runner-mcp
```

**(2) Prepare Python environment (Python 3.12+ recommended):**
```bash
# Create a virtual environment (e.g., venv312)
python -m venv venv312
# Activate the virtual environment
# Windows PowerShell
.\venv312\Scripts\Activate
# Linux/macOS
source venv312/bin/activate
```

**(3) Install dependencies:**
Install using the requirements.txt file.
```bash
pip install -r requirements.txt
```

**(4) Install Playwright browsers:**
```bash
playwright install
```

### 2. Starting the Server (SSE Mode Example)
**Note: This mode has not been fully verified and might require adjustments.**
To allow access over the network (e.g., for Dify integration), start the server in SSE mode.

```bash
# Run web_runner_mcp_server.py directly
python web_runner_mcp_server.py --transport sse --host 0.0.0.0 --port 8000
```
*   Use `--host 0.0.0.0` to allow access from other machines. Use `127.0.0.1` (default) for local access only.
*   `--port 8000` specifies the port the server listens on.
*   Server logs are output to `web_runner_mcp_server.log` (default setting).

### 3. Creating JSON Data for Web-Runner
You can use the included `json_generator.html` to interactively create the JSON file in your browser.

#### Step 1: Prepare the JSON Generator
1. Open the `json_generator.html` file located in the project folder with your web browser (double-click).

#### Step 2: Get CSS Selectors for Target Elements
1. Open the target website you want to automate in a separate browser tab or window.
2. Open the developer tools on that page (usually F12 key or right-click > "Inspect"/"Inspect Element").
3. Click the element selection icon (↖) in the developer tools.
4. Click the element you want to interact with (button, input field, etc.) on the webpage.
5. In the developer tools, right-click the highlighted HTML element and select [Copy] > [Copy selector].

#### Step 3: Create Operation Steps in json_generator.html
1. Go back to the `json_generator.html` tab.
2. Enter the website's URL in "1. Target URL:".
3. In "2. Operation Steps", fill in the following:
    *   Target Element CSS Selector: Paste the selector you copied.
    *   Operation: Choose the desired action.
    *   Additional Parameters: Enter values if needed (e.g., `value`, `attribute_name`).
4. Click "Add Step" and repeat step 3 for all required actions.
5. Click "Generate JSON Data" to see the generated JSON.
6. Click "Download input.json" to save the JSON file.

#### Step 4: Place the JSON File
1. Move the downloaded JSON file into the `json/` folder within the project directory. You can rename the file as needed (e.g., `my_task.json`).

### 4. Command-Line Execution (for Testing)
You can test the Web-Runner directly from the command line using the core client function (`web_runner_mcp_client_core.py`) without the GUI. This is useful for verifying programmatic calls, like those from an AI agent.
1. Ensure your desired JSON file is in the `json/` folder (e.g., `tdnet.json`).
2. Run the following command in your activated terminal:
```bash
python web_runner_mcp_client_core.py --jsonfile json/tdnet.json --no-headless --slowmo 500
```
*   `--jsonfile`: Specifies the path to the JSON file to execute (default: `json/tdnet.json`).
*   `--no-headless`: Use this flag to display the browser during execution (default is visible). Use `--headless` to run in the background.
*   `--slowmo`: (Optional) Adds a delay (in milliseconds) between operations (e.g., `--slowmo 500`).
*   `--output`: (Optional) Specifies the path for the output file (default: `output_web_runner.txt`).

The execution results (successful data retrieval or error information) will be printed to the console in JSON format and also written to the specified output file.

### 5. Running from the GUI Client
For manual testing and debugging, the GUI client (`web_runner_mcp_client_GUI.py`) is convenient.
1. Run the following command in your activated terminal:
```bash
python web_runner_mcp_client_GUI.py
```
2. In the application window, select the desired JSON file from the dropdown list.
3. Click the "実行 ▶" (Run) button.
4. The execution results will be displayed in the text area below.
5. You can also click the "JSONジェネレーター" (JSON Generator) button to open `json_generator.html`.

### 6. Usage from AI Applications
To use Web-Runner-mcp from other Python scripts or AI agent frameworks, import and use the `execute_web_runner_via_mcp` function from `web_runner_mcp_client_core.py`.

```python
import asyncio
import json
import sys # Add sys import
# Ensure web_runner_mcp_client_core.py is in the import path
try:
    from web_runner_mcp_client_core import execute_web_runner_via_mcp
except ImportError:
    print("Error: web_runner_mcp_client_core.py not found.")
    # Error handling or path configuration needed
    sys.exit(1) # Example

async def run_task():
    input_data = {
        "target_url": "https://example.com",
        "actions": [
            {"action": "get_text_content", "selector": "h1"},
            {"action": "get_attribute", "selector": "img", "attribute_name": "src"}
        ]
        # Optionally specify timeouts etc.
        # "default_timeout_ms": 15000
    }
    # Execute in headless mode with 50ms slow motion
    success, result_or_error = await execute_web_runner_via_mcp(
        input_data, headless=True, slow_mo=50 # Specify headless, slow_mo
    )

    if success and isinstance(result_or_error, str):
        print("Task successful! Result (JSON):")
        try:
            result_dict = json.loads(result_or_error)
            print(json.dumps(result_dict, indent=2, ensure_ascii=False))
            # --- Process the results, potentially pass to an LLM ---
            # llm_prompt = f"Analyze the following website operation results:\n```json\n{result_or_error}\n```"
            # llm_response = await call_llm(llm_prompt)
        except json.JSONDecodeError:
            print("Error: Response from server is not valid JSON:")
            print(result_or_error)
    else:
        print("Task failed:")
        print(result_or_error) # Display error information (dictionary)
        # --- Process the error information, potentially pass to an LLM ---
        # error_prompt = f"Website operation failed. Error details:\n{result_or_error}\nInfer the cause."
        # llm_response = await call_llm(error_prompt)

if __name__ == "__main__":
    asyncio.run(run_task())
```

## JSON Format (Reference)
Refer to the JSON files provided in the `json/` folder for examples.
Here is the basic structure of the input JSON:
```json
{
  "target_url": "Starting URL (e.g., https://www.example.com)",
  "actions": [
    {
      "action": "Action name (e.g., click)",
      "selector": "CSS selector (required for element actions)",
      "value": "Input value, wait time, etc. (depends on action)",
      "attribute_name": "Attribute to get (for get_attribute actions)",
      "option_type": "Dropdown selection type (for select_option)",
      "option_value": "Dropdown selection value (for select_option)",
      "wait_time_ms": "Action-specific timeout (optional)",
      "iframe_selector": "Iframe selector (for switch_to_iframe)"
    },
    // ... other action steps ...
  ]
  // Options (can be specified when calling the tool)
  // "headless": true, // Overrides client's headless setting if provided
  // "slow_mo": 100,   // Overrides client's slow_mo setting if provided
  // "default_timeout_ms": 15000 // Overrides the default action timeout
}
```

## Comparison with Other Tools
*   **General Web Scraping Libraries (BeautifulSoup, Scrapy):** Excellent for parsing static HTML, but struggle with or cannot handle JavaScript execution, logins, complex user interactions, iframes, and PDFs. Web-Runner-mcp, being Playwright-based, handles these advanced operations.
*   **Playwright-MCP:** Exposes Playwright's low-level API directly as MCP tools. Highly flexible, but requires complex prompt engineering and state management for reliable control from LLMs. Web-Runner-mcp offers a more declarative and reliable interface by defining operation sequences in JSON.
*   **Simple Web Fetching Tools (e.g., URL content fetchers):** Easy for getting content from a single URL, but incapable of multi-step operations or interactions. Web-Runner-mcp executes multi-step workflows.

## Future Plans
*   **LLM-Powered JSON Generation:** Integrate functionality to automatically generate Web-Runner JSON from natural language instructions.
*   **Expanded Action Support:** Add support for more Playwright features (e.g., file uploads, cookie manipulation).
*   **Official Dify Custom Tool Support:** Stabilize the HTTP/SSE interface aiming for potential registration in the Dify marketplace.
*   **Enhanced Error Handling and Recovery:** Implement more detailed error analysis and potentially automatic retry/recovery mechanisms.

## Contributing
Bug reports, feature suggestions, and pull requests are welcome! Please see CONTRIBUTING.md for details (to be created if not present).

## License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.