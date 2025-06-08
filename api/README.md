# WhatsApp Chatbot API - FastAPI + DSPy

API FastAPI intelligente pour le chatbot WhatsApp utilisant DSPy, Mistral AI et outils de recherche avancés.

## 🚀 Fonctionnalités

- **IA conversationnelle** avec DSPy ReAct agent
- **Recherche web** via MechanicalSoup (DuckDuckGo)
- **Recherche Wikipedia** avec métadonnées
- **Validation IA** des entrées/sorties (anti-injection)
- **Historique persistant** SQLite
- **API REST** complète avec FastAPI

## 📦 Installation

```bash
cd api
pip install -r requirements.txt
```

## ⚙️ Configuration

Créer `.env` :
```env
MISTRAL_API_KEY=votre_cle_mistral_api
```

## 🎯 Architecture

### Composants principaux

```python
# Agent principal avec outils de recherche
class WhatsAppChatBot(dspy.Module):
    def __init__(self):
        self.generate_response = dspy.ReAct(
            tools=[mecha_search_tool, wiki_search_tool]
        )
        self.input_validator = InputValidator()
        self.output_validator = OutputValidator()
```

### Outils de recherche

- **`mecha_search_tool`** : Recherche DuckDuckGo + extraction métadonnées
- **`wiki_search_tool`** : API Wikipedia avec extraits et URLs

### Validation IA

- **`InputValidator`** : Filtre les prompts malveillants
- **`OutputValidator`** : Valide les réponses générées

## 📊 Endpoints API

### `POST /chat`
Traitement principal des messages
```json
{
  "message": "Que sais-tu sur Paris ?",
  "user_id": "33123456789",
  "user_name": "Alice",
  "is_group": false,
  "is_mentioned": false
}
```

### `GET /health`
Vérification état API

### `GET /stats`
Statistiques d'usage
```json
{
  "total_users": 15,
  "total_conversations": 234,
  "active_users": 3
}
```

### `DELETE /history/{user_id}`
Effacer historique utilisateur

## 🗄️ Base de données

Table SQLite `conversations` :
```sql
CREATE TABLE conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    user_message TEXT NOT NULL,
    bot_response TEXT NOT NULL,
    timestamp TEXT NOT NULL
);
```

## 🔧 Personnalisation

### Changer de modèle LLM
```python
# Mistral (défaut)
lm = dspy.LM("openai/mistral-small-latest", api_key="...")

# OpenAI
lm = dspy.LM("gpt-4", api_key="...")

# Ollama local
lm = dspy.LM('ollama_chat/llama3', api_base='http://localhost:11434')
```

### Ajouter des outils personnalisés
```python
def custom_tool(query: str) -> str:
    # Votre logique ici
    return f"Résultat pour {query}"

# Dans WhatsAppChatBot.__init__()
self.generate_response = dspy.ReAct(
    tools=[mecha_search_tool, wiki_search_tool, custom_tool]
)
```

## 🚀 Démarrage

```bash
python main.py
```

API disponible sur `http://localhost:8000`

Documentation interactive : `http://localhost:8000/docs`

## 🔍 Fonctionnement des outils

### Recherche web (DuckDuckGo)
1. Recherche sur DuckDuckGo HTML
2. Extraction métadonnées pour chaque résultat :
   - Titre de la page
   - Meta description
   - Date de publication
3. Formatage structuré pour l'IA

### Recherche Wikipedia
1. API MediaWiki pour recherche par mots-clés
2. Extraction pour chaque article :
   - Titre complet
   - URL canonique
   - Extrait introductif (500 chars)
   - Date dernière modification
3. Résultats limités à 5 articles

## 🛡️ Sécurité

### Validation d'entrée
```python
class InputValidator(dspy.Module):
    # Détecte et bloque :
    # - Injections de prompts
    # - Contenu malveillant
    # - Tentatives d'exploitation
```

### Validation de sortie
```python
class OutputValidator(dspy.Module):
    # Vérifie que les réponses sont :
    # - Appropriées
    # - Sans contenu sensible
    # - Conformes aux guidelines
```

## 📈 Monitoring

### Logs DSPy
```python
# Inspection des interactions
print(dspy.inspect_history(2))
```

### Métriques disponibles
- Nombre d'utilisateurs total/actifs
- Volume de conversations
- Historique par utilisateur

## 🔧 Développement

### Tests API
```bash
curl -X POST "http://localhost:8000/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Salut !",
    "user_id": "test123",
    "user_name": "Test"
  }'
```

### Debug mode
```python
# Dans main.py
uvicorn.run("main:app", reload=True, log_level="debug")
```