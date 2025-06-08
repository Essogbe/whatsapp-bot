from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import dspy
import requests
import mechanicalsoup
import sqlite3
from bs4 import BeautifulSoup
import uvicorn
from datetime import datetime
from typing import Dict, List, Optional

from dotenv import load_dotenv
import os
# Chargement des variables d'environnement depuis le fichier .env
load_dotenv()

app = FastAPI(title="WhatsApp Chatbot API", version="1.0.0")

# ┌────────────────────────────────────────────────────────┐
# │         INITIALISATION DE LA BASE DE DONNÉES SQLite   │
# └────────────────────────────────────────────────────────┘
DB_PATH = "chat_history.db"

def init_db():
    """
    Crée la table `conversations` si elle n'existe pas.
    Schéma :
      - id (INTEGER PRIMARY KEY AUTOINCREMENT)
      - conversation_id (TEXT)
      - user_message (TEXT)
      - bot_response (TEXT)
      - timestamp (TEXT, ISO format)
    """
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL,
            user_message TEXT NOT NULL,
            bot_response TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn

# Connexion SQLite partagée
db_conn = init_db()

# ┌────────────────────────────────────────────────────────┐
# │       DÉFINITION DU TOOL 1 : RECHERCHE AVEC MECHANICALSOUP       │
# └────────────────────────────────────────────────────────┘
def fetch_page_meta(url: str) -> Dict[str, str]:
    """
    Récupère les métadonnées d’une page (titre, meta description, date de publication si présente).
    Utilise requests + BeautifulSoup. Retourne un dict avec :
      - title
      - meta_description (vide si introuvable)
      - date (vide si pas de <meta name="date"> ou <meta property="article:published_time">)
    """
    try:
        resp = requests.get(url, timeout=5, headers={"User-Agent": "dspy-agent/0.1"})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        # Titre
        title_tag = soup.find("title")
        title = title_tag.get_text().strip() if title_tag else ""
        # meta description
        meta_desc = soup.find("meta", {"name": "description"})
        meta_description = meta_desc["content"].strip() if (meta_desc and meta_desc.get("content")) else ""
        # date de publication (méta standard “article:published_time” ou “date”)
        date_meta = soup.find("meta", {"property": "article:published_time"}) or soup.find("meta", {"name": "date"})
        date_pub = date_meta["content"].strip() if (date_meta and date_meta.get("content")) else ""
        return {"title": title, "meta_description": meta_description, "date": date_pub}
    except Exception:
        return {"title": "", "meta_description": "", "date": ""}


def search_mecha(query: str, num_results: int = 3) -> str:
    """
    Lance une recherche sur DuckDuckGo (HTML).
    Pour chaque résultat (titre + URL), on tente de récupérer les métadonnées (titre, meta description, date).
    Retourne un texte formaté avec :
      - Titre (de DuckDuckGo ou titre de la page si disponible)
      - URL
      - Meta description (si disponible)
      - Date de publication (si disponible)
    """
    browser = mechanicalsoup.StatefulBrowser(user_agent="dspy-agent/0.1")
    browser.open("https://duckduckgo.com/html/")
    browser.select_form('form[id="search_form_homepage"]')
    browser["q"] = query
    browser.submit_selected()

    items = browser.page.select("a.result__a")[:num_results]
    results = []
    for a in items:
        # Titre et URL renvoyés par DuckDuckGo
        ddg_title = a.get_text().strip()
        href = a.get("href")

        # Récupérer les métadonnées depuis la page cible
        meta = fetch_page_meta(href)
        title = meta["title"] or ddg_title
        desc = meta["meta_description"]
        date_pub = meta["date"]

        entry_lines = [f"- {title} — {href}"]
        if desc:
            entry_lines.append(f"  Meta-description : {desc}")
        if date_pub:
            entry_lines.append(f"  Date de publication : {date_pub}")
        results.append("\n".join(entry_lines))

    browser.close()
    return "\n\n".join(results)


def mecha_search_tool(input_text: str) -> str:
    """
    Wrapper pour exposer `search_mecha` comme tool pour dspy.ReAct.
    Limite les résultats pour contrôler la consommation de tokens.
    """
    return search_mecha(input_text, num_results=3)


# ┌────────────────────────────────────────────────────────┐
# │       DÉFINITION DU TOOL 2 : RECHERCHE SUR WIKIPÉDIA    │
# └────────────────────────────────────────────────────────┘
def wiki_search_tool(query: str) -> str:
    """
    Interroge l'API MediaWiki pour :
      1. Rechercher les 5 pages les plus pertinentes (action=query&list=search&srsearch& srlimit=5).
      2. Pour chaque titre retourné, récupérer le résumé introductif, l'URL complète et la date de dernière modification.
    Renvoie un texte formaté listant pour chaque page :
      - Titre de l'article
      - URL
      - Extrait introductif (250 caractères max)
      - Date de dernière modification
    """
    S = requests.Session()
    API_URL = "https://fr.wikipedia.org/w/api.php"

    # 1) Recherche des 5 titres les plus pertinents
    params_search = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "format": "json",
        "srlimit": 5
    }
    try:
        resp = S.get(API_URL, params=params_search, timeout=5)
        resp.raise_for_status()
        search_results = resp.json().get("query", {}).get("search", [])
        if not search_results:
            return f"Aucun résultat trouvé pour « {query} » sur Wikipédia."
    except Exception:
        return f"Erreur lors de la recherche Wikipédia pour « {query} »."

    entries = []
    # 2) Pour chaque titre, extraire l'intro et l'URL
    for result in search_results:
        page_title = result.get("title")
        params_extract = {
            "action": "query",
            "prop": "extracts|info|revisions",
            "exintro": True,
            "explaintext": True,
            "inprop": "url",
            "rvprop": "timestamp",
            "titles": page_title,
            "format": "json"
        }
        try:
            resp2 = S.get(API_URL, params=params_extract, timeout=5)
            resp2.raise_for_status()
            pages = resp2.json().get("query", {}).get("pages", {})
            page_info = next(iter(pages.values()))
            extract = page_info.get("extract", "").strip().replace("\n", " ")
            extract_snippet = extract[:500].rstrip() + ("..." if len(extract) > 500 else "") # Limite à 500 caractères
            full_url = page_info.get("fullurl", "")
            # Date de dernière révision (UTC ISO)
            date_modif = ""
            revisions = page_info.get("revisions", [])
            if revisions:
                date_modif = revisions[0].get("timestamp", "")
        except Exception:
            # En cas d'erreur pour cette page, on affiche un message succinct
            entries.append(
                f"- Titre Wikipédia : {page_title}\n"
                f"  Erreur lors de la récupération des détails pour cet article."
            )
            continue

        entry = (
            f"- Titre Wikipédia : {page_title}\n"
            f"  URL : {full_url}\n"
            f"  Extrait : {extract_snippet}\n"
            f"  Dernière modification : {date_modif}"
        )
        entries.append(entry)

    # Concaténer tous les résultats en les séparant par deux sauts de ligne
    return "\n\n".join(entries)


# ┌────────────────────────────────────────────────────────┐
# │    CONFIGURATION DSPy / LLM (Mistral via dspy)          │
# └────────────────────────────────────────────────────────┘
lm = dspy.LM(
    "openai/mistral-small-latest",
    api_key=os.environ.get("MISTRAL_API_KEY"),
    api_base="https://api.mistral.ai/v1",
    max_tokens=20000
)
dspy.settings.configure(lm=lm)

# ┌────────────────────────────────────────────────────────┐
# │ SIGNATURE ET MODULE : VALIDATEUR D'ENTRÉE AI-DRIVEN     │
# └────────────────────────────────────────────────────────┐
class InputValidationSignature(dspy.Signature):
    user_message = dspy.InputField(desc="Message de l'utilisateur à valider")
    is_safe = dspy.OutputField(desc="True si le message est sécurisé, False sinon")

class InputValidator(dspy.Module):
    def __init__(self):
        super().__init__()
        self.classify_input = dspy.ChainOfThought(InputValidationSignature)

    def forward(self, user_message: str) -> bool:
        result = self.classify_input(user_message=user_message)
        reply = result.is_safe.strip().lower()
        return reply == "true"

# ┌────────────────────────────────────────────────────────┐
# │ SIGNATURE ET MODULE : VALIDATEUR DE SORTIE AI-DRIVEN    │
# └────────────────────────────────────────────────────────┐
class OutputValidationSignature(dspy.Signature):
    bot_response = dspy.InputField(desc="Réponse brute du modèle à valider")
    is_safe = dspy.OutputField(desc="True si la réponse est sécurisée, False sinon")

class OutputValidator(dspy.Module):
    def __init__(self):
        super().__init__()
        self.classify_output = dspy.ChainOfThought(OutputValidationSignature)

    def forward(self, bot_response: str) -> bool:
        result = self.classify_output(bot_response=bot_response)
        reply = result.is_safe.strip().lower()
        return reply == "true"

# ┌────────────────────────────────────────────────────────┐
# │     DÉFINITION DE LA SIGNATURE POUR LE CHATBOT DSPY     │
# └────────────────────────────────────────────────────────┐
class ChatBotSignature(dspy.Signature):
    user_message = dspy.InputField(desc="Message de l'utilisateur")
    user_name = dspy.InputField(desc="Nom de l'utilisateur")
    context = dspy.InputField(desc="Contexte de la conversation")
    is_group = dspy.InputField(desc="True si c'est un message de groupe")
    is_mentioned = dspy.InputField(desc="True si le bot a été mentionné")
    response = dspy.OutputField(desc="Réponse personnalisée et adaptée au contexte")

# ┌────────────────────────────────────────────────────────┐
# │    FONCTIONS DE GESTION DE L'HISTORIQUE (SQLite)       │
# └────────────────────────────────────────────────────────┐
def get_conversation_context_sql(user_id: str, is_group: bool, limit: int = 5) -> str:
    """
    Récupère les derniers `limit` échanges pour un `conversation_id` donné depuis SQLite,
    et reconstruit le contexte sous forme de string.
    """
    conversation_id = f"{user_id}{'_group' if is_group else ''}"
    cursor = db_conn.cursor()
    cursor.execute("""
        SELECT user_message, bot_response
        FROM conversations
        WHERE conversation_id = ?
        ORDER BY id DESC
        LIMIT ?
    """, (conversation_id, limit))
    rows = cursor.fetchall()
    if not rows:
        return "Nouvelle conversation" + (" de groupe" if is_group else "")

    rows.reverse()
    context_lines = []
    for user_msg, bot_resp in rows:
        context_lines.append(f"User: {user_msg}\nBot: {bot_resp}")
    context_type = "groupe" if is_group else "privée"
    return f"Historique récent (conversation {context_type}):\n" + "\n".join(context_lines)

def update_conversation_history_sql(user_id: str, user_msg: str, bot_response: str, is_group: bool):
    """
    Ajoute un nouvel échange dans la table `conversations`.
    Conserve toutes les entrées ; la limite sera gérée lors de la récupération du contexte.
    """
    conversation_id = f"{user_id}{'_group' if is_group else ''}"
    timestamp = datetime.now().isoformat()
    cursor = db_conn.cursor()
    cursor.execute("""
        INSERT INTO conversations (conversation_id, user_message, bot_response, timestamp)
        VALUES (?, ?, ?, ?)
    """, (conversation_id, user_msg, bot_response, timestamp))
    db_conn.commit()

# ┌────────────────────────────────────────────────────────┐
# │           MODULE PRINCIPAL DU CHATBOT                  │
# └────────────────────────────────────────────────────────┐
class WhatsAppChatBot(dspy.Module):
    def __init__(self):
        super().__init__()
        # Agent ReAct avec deux tools : mecha_search_tool et wiki_search_tool
        self.generate_response = dspy.ReAct(
            signature="user_message, user_name, context, is_group, is_mentioned -> response: str",
            tools=[mecha_search_tool, wiki_search_tool]
        )
        self.input_validator = InputValidator()
        self.output_validator = OutputValidator()

    def forward(self,
                user_message: str,
                user_name: str,
                user_id: str,
                is_group: bool = False,
                is_mentioned: bool = False) -> str:
        """
        1. Validation de l'entrée via InputValidator.
        2. Construction du contexte via SQLite.
        3. Appel de l'agent ReAct (avec les deux tools disponibles).
        4. Validation de la sortie via OutputValidator.
        5. Mise à jour de l'historique dans SQLite.
        """
        # 1) Validation AI-driven de l'entrée
        is_safe_input = self.input_validator.forward(user_message)
        if not is_safe_input:
            return "🚫 Votre message a été bloqué car il contient du contenu potentiellement dangereux ou une tentative de prompt injection."

        # 2) Préparation du contexte depuis SQLite
        context = get_conversation_context_sql(user_id, is_group)
        if is_group and is_mentioned:
            context += "\n[GROUPE - BOT MENTIONNÉ] Réponds de manière concise."
        elif is_group:
            context += "\n[GROUPE - SANS MENTION] Message de groupe général."

        # 3) Appel à l’agent ReAct (avec mecha_search_tool et wiki_search_tool)
        result = self.generate_response(
            user_message=user_message,
            user_name=user_name,
            context=context,
            is_group=str(is_group),
            is_mentioned=str(is_mentioned)
        )
        bot_reply = result.response

        # # 4) Validation AI-driven de la sortie
        # is_safe_output = self.output_validator.forward(bot_reply)
        #

        # 5) Mise à jour de l'historique dans SQLite
        update_conversation_history_sql(user_id, user_message, bot_reply, is_group)

        # (Facultatif) : inspecter l'historique (dernier échange)
        print(dspy.inspect_history(2))

        return bot_reply

# Instanciation du chatbot
chatbot = WhatsAppChatBot()

# ┌────────────────────────────────────────────────────────┐
# │                FASTAPI ENDPOINTS                      │
# └────────────────────────────────────────────────────────┘
class ChatRequest(BaseModel):
    message: str
    user_id: str
    user_name: str
    is_group: bool = False
    is_mentioned: bool = False
    participant_id: Optional[str] = None

class ChatResponse(BaseModel):
    response: str
    timestamp: str

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    """Endpoint principal pour traiter les messages WhatsApp."""
    try:
        response_text = chatbot.forward(
            user_message=request.message,
            user_name=request.user_name,
            user_id=request.user_id,
            is_group=request.is_group,
            is_mentioned=request.is_mentioned
        )
        return ChatResponse(
            response=response_text,
            timestamp=datetime.now().isoformat()
        )
    except Exception as e:
        print(f"Erreur traitement message: {e}")
        raise HTTPException(
            status_code=500,
            detail="Erreur lors du traitement du message"
        )

@app.get("/health")
async def health_check():
    """Vérification de l'état de l'API."""
    try:
        return {
            "status": "healthy",
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }

@app.get("/stats")
async def get_stats():
    """Renvoie des statistiques basiques sur l'historique."""
    cursor = db_conn.cursor()
    # Nombre d'utilisateurs distincts (conversation_id unique)
    cursor.execute("SELECT COUNT(DISTINCT conversation_id) FROM conversations")
    total_users = cursor.fetchone()[0] or 0

    # Nombre total de messages stockés (échanges)
    cursor.execute("SELECT COUNT(*) FROM conversations")
    total_conversations = cursor.fetchone()[0] or 0

    # Nombre d'utilisateurs actifs (dernier échange < 1h)
    one_hour_ago = (datetime.now().replace(microsecond=0) -
                    datetime.utcfromtimestamp(0)).isoformat()
    cursor.execute("""
        SELECT COUNT(DISTINCT conversation_id)
        FROM conversations
        WHERE timestamp >= ?
    """, (one_hour_ago,))
    active_users = cursor.fetchone()[0] or 0

    return {
        "total_users": total_users,
        "total_conversations": total_conversations,
        "active_users": active_users
    }

@app.delete("/history/{user_id}")
async def clear_user_history(user_id: str):
    """Effacer l'historique d'un utilisateur (toutes ses lignes dans SQLite)."""
    for suffix in ("", "_group"):
        conversation_id = f"{user_id}{suffix}"
        cursor = db_conn.cursor()
        cursor.execute("DELETE FROM conversations WHERE conversation_id = ?", (conversation_id,))
    db_conn.commit()
    return {"message": f"Historique effacé pour {user_id}"}

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
