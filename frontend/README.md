# Start the Frontend

Run these commands from the project root:

1. Install the dependencies:

   ```powershell
   pip install -r requirements.txt
   ```

2. Make sure `data/index.json` exists. For local AI summaries, also provide
   `data/raw_pages.json` and configure `GEMINI_API_KEY` in
   `.streamlit/secrets.toml`.

3. Start Streamlit:

   ```powershell
   streamlit run frontend/app.py
   ```

   Alternatively:

   ```powershell
   python -m streamlit run frontend/app.py
   ```

4. Open <http://127.0.0.1:8501> in a browser.
