from firebase_functions import https_fn
from firebase_admin import initialize_app, firestore
import random
import json

initialize_app()

VENUES_DATA = {
    "biblioteca":        {"role": "Bibliotecário",   "topics": ["Culture","Geography","History","Infrastructure"]},
    "cartografo":        {"role": "Guia Local",       "topics": ["Geographical Aspects", "Settlement Type"]},
    "centro_cultural":   {"role": "Guia Local",       "topics": ["Culture","Geography","History"]},
    "estalagem":         {"role": "Estalajadeiro",    "topics": ["Culture","Infrastructure","Politics"]},
    "estaleiro":         {"role": "Mestre do Cais",   "topics": ["Trade"]},
    "museu":             {"role": "Curador",          "topics": ["Culture","Geography","History"]},
    "oficina_gemas":     {"role": "Mestre Joalheiro", "topics": ["Geographical Aspects","Settlement Type"]},
    "patio_carrocas":    {"role": "Mestre de Carga",  "topics": ["Culture","Geography","History"]},
    "patio_treinamento": {"role": "Mestre d'Armas",   "topics": ["Defense", "Races"]},
    "santuario":         {"role": "Sacerdote",        "topics": ["Religion"]},
    "taverna":           {"role": "Taverneiro",       "topics": ["Culture","Geography","Politics"]},
    "torre_alta_magia":  {"role": "Arcanista",        "topics": ["Magic"]},
}

VENUE_IDS = list(VENUES_DATA.keys())

CRIMINALS_DATA = {
  "black_spider":           {"gender":"M", "hair":"claro",   "feature":"um cajado",    "hobby":"xadrez", "vehicle":"voando",                         "cuisine":"vegetais",          "species":"élfica"},
  "glassstaff":             {"gender":"M", "hair":"escuro",  "feature":"um cajado",    "hobby":"xadrez", "vehicle":"voando",                         "cuisine":"vegetais",          "species":"humana"},
  "halia_thornton":         {"gender":"F", "hair":"escuro",  "feature":"belas joias",  "hobby":"cartas", "vehicle":"em uma carruagem",               "cuisine":"comida apimentada", "species":"humana"},
  "jarlaxe_baenre":         {"gender":"M", "hair":"raspado", "feature":"belas joias",  "hobby":"cartas", "vehicle":"dirigindo uma máquina infernal", "cuisine":"frutos do mar",     "species":"élfica"},
  "lord_drylund":           {"gender":"M", "hair":"claro",   "feature":"uma tatuagem", "hobby":"dados",  "vehicle":"em uma carruagem",               "cuisine":"doces",             "species":"humana"},
  "nass_lantomir":          {"gender":"F", "hair":"ruivo",   "feature":"um cajado",    "hobby":"cartas", "vehicle":"voando",                         "cuisine":"frutos do mar",     "species":"humana"},
  "nine_fingers_keene":     {"gender":"F", "hair":"escuro",  "feature":"belas joias",  "hobby":"dados",  "vehicle":"em uma carruagem",               "cuisine":"doces",             "species":"humana"},
  "prisioner_13":           {"gender":"F", "hair":"ruivo",   "feature":"uma tatuagem", "hobby":"dados",  "vehicle":"dirigindo uma máquina infernal", "cuisine":"comida apimentada", "species":"anã"},
  "valindra_shadowmantle":  {"gender":"F", "hair":"claro",   "feature":"belas joias",  "hobby":"xadrez", "vehicle":"voando",                         "cuisine":"frutos do mar",     "species":"élfica"},
  "xardorok_sunblight":     {"gender":"M", "hair":"claro",   "feature":"uma tatuagem", "hobby":"dados",  "vehicle":"em uma carruagem",               "cuisine":"comida apimentada", "species":"anã"}
}

CRIMINAL_IDS = list(CRIMINALS_DATA.keys())

def handle_cors(req: https_fn.Request):
    """Trata preflight CORS para todas as rotas."""
    if req.method == "OPTIONS":
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Max-Age": "3600",
        }
        return https_fn.Response("", status=204, headers=headers), True
    return {"Access-Control-Allow-Origin": "*"}, False


def get_valid_session(db, session_id):
    """Retorna (ref, dados) da sessão ou (None, None) se inválida."""
    if not session_id:
        return None, None
    session_ref = db.collection("sessions").document(session_id)
    session_doc = session_ref.get()
    if not session_doc.exists:
        return None, None
    return session_ref, session_doc.to_dict()


def _build_travel_options(trail_ids, current_step, current_location, history, distractors):
    """
    Regras de opções de viagem:
    - Fora da trilha: APENAS a cidade de retorno (history[-2]), forçando correção
      antes de qualquer nova escolha.
    - Na trilha: próxima cidade correta + retorno (se existir) + distratoras
      (nunca cidades pertencentes a trilha).
    """
    on_trail = current_location == trail_ids[current_step]

    if not on_trail:
        return [history[-2]] if len(history) > 1 else []

    options = []

    if current_step < len(trail_ids) - 1:
        options.append(trail_ids[current_step + 1])

    if len(history) > 1:
        back_city = history[-2]
        if back_city not in options:
            options.append(back_city)

    safe_distractors = [d for d in distractors if d not in trail_ids and d not in options]
    options = options + safe_distractors[:5 - len(options)]

    random.shuffle(options)
    return options

@https_fn.on_request()
def start_game(req: https_fn.Request) -> https_fn.Response:
    cors_headers, is_options = handle_cors(req)
    if is_options:
        return cors_headers

    db = firestore.client()
    try:
        body = req.get_json(silent=True) or {}
        password_attempt = body.get("password", "")
        player_name = body.get("playerName", "")

        config_ref = db.collection("config").document("access")
        config_doc = config_ref.get()
        config = config_doc.to_dict() if config_doc.exists else {}

        # Valida senha
        expected_password = config.get("password", "")
        if not expected_password or password_attempt != expected_password:
            return https_fn.Response(
                json.dumps({"error": "Senha incorreta."}),
                status=401, mimetype="application/json", headers=cors_headers
            )

        max_per_hour = config.get("max_sessions_per_hour", 20)
        from datetime import datetime, timezone, timedelta
        one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
        recent_docs = db.collection("sessions") \
            .where("start_time", ">=", one_hour_ago) \
            .select([]) \
            .stream()
        session_count = sum(1 for _ in recent_docs)

        if session_count >= max_per_hour:
            return https_fn.Response(
                json.dumps({"error": "Limite de partidas por hora atingido. Tente novamente mais tarde."}),
                status=429, mimetype="application/json", headers=cors_headers
            )

        all_city_ids = [d.to_dict()["id"] for d in db.collection("cities").select(["id"]).stream()]
        criminal_id = random.choice(CRIMINAL_IDS)
        trail_ids = random.sample(all_city_ids, 6)
        non_trail_ids = [c for c in all_city_ids if c not in trail_ids]

        venues_per_city = {}
        distractors_per_city = {}
        for city_id in trail_ids:
            venues_per_city[city_id] = random.sample(VENUE_IDS, 3)
            distractors_per_city[city_id] = random.sample(non_trail_ids, min(4, len(non_trail_ids)))

        session_ref = db.collection("sessions").document()
        session_ref.set({
            "criminal_id": criminal_id,
            "trail": trail_ids,
            "current_step": 0,
            "current_location": trail_ids[0],
            "start_time": firestore.SERVER_TIMESTAMP,
            "venues_per_city": venues_per_city,
            "distractors_per_city": distractors_per_city,
            "used_curiosities_per_city": {},
            "player": player_name,
        })

        return https_fn.Response(
            json.dumps({
                "sessionId": session_ref.id,
                "firstCityId": trail_ids[0],
                "venues": venues_per_city[trail_ids[0]],
                "travelOptions": _build_travel_options(
                    trail_ids=trail_ids,
                    current_step=0,
                    current_location=trail_ids[0],
                    history=[trail_ids[0]],
                    distractors=distractors_per_city[trail_ids[0]],
                ),
            }),
            mimetype="application/json",
            headers=cors_headers
        )
    except Exception as e:
        return https_fn.Response(
            json.dumps({"error": str(e)}), status=500, headers=cors_headers
        )


@https_fn.on_request()
def investigate(req: https_fn.Request) -> https_fn.Response:
    cors_headers, is_options = handle_cors(req)
    if is_options:
        return cors_headers

    db = firestore.client()
    try:
        data = req.get_json()
        session_id = data.get("sessionId")
        session_ref, session = get_valid_session(db, session_id)

        if not session:
            return https_fn.Response(
                json.dumps({"error": "Sessao nao encontrada ou expirada."}),
                status=404, mimetype="application/json", headers=cors_headers
            )

        venue_id = data.get("venueId")
        current_step = session["current_step"]
        trail = session["trail"]
        criminal_id = session["criminal_id"]
        current_location = session.get("current_location")

        if current_location != trail[current_step]:
            clues_wrong_track = [
                "As ruas estão calmas, ninguém suspeito passou por aqui.",
                "Não vi ninguém com essa descrição. Você deve ter se perdido no caminho.",
                "Acho que você está procurando no lugar errado, forasteiro.",
            ]
            return https_fn.Response(
                json.dumps({"clue": random.choice(clues_wrong_track), "captured": False}),
                mimetype="application/json", headers=cors_headers
            )

        criminal = CRIMINALS_DATA[criminal_id]

        if current_step == len(trail) - 1:
            used_in_final = session.get("used_curiosities_per_city", {}).get(trail[current_step], [])
            attempts = len(used_in_final)
            capture_probability = 0.8 if attempts == 0 else (0.6 if attempts == 1 else 1.0)

            session_ref.update({
                f"used_curiosities_per_city.{trail[current_step]}": used_in_final + [venue_id]
            })

            return https_fn.Response(
                json.dumps({
                    "clue": "O suspeito foi visto por aqui há poucos minutos!",
                    "captured": random.random() < capture_probability,
                }),
                mimetype="application/json", headers=cors_headers
            )

        next_city_id = trail[current_step + 1]
        next_city = db.collection("cities").document(next_city_id).get().to_dict()
        curiosities_map = next_city.get("curiosities", {})
        venue_data = VENUES_DATA.get(venue_id, {})
        role = venue_data.get("role", "encarregado")
        venue_topics = venue_data.get("topics", [])

        used_curiosities = session.get("used_curiosities_per_city", {}).get(current_location, [])
        venue_curiosities = [curiosities_map[t] for t in venue_topics if t in curiosities_map]
        available = [c for c in venue_curiosities if c not in used_curiosities]

        if not available:
            available = venue_curiosities
        if not available:
            available = [c for c in curiosities_map.values() if c not in used_curiosities]
        if not available:
            available = list(curiosities_map.values())

        lead = random.choice(available)

        session_ref.update({
            f"used_curiosities_per_city.{current_location}": used_curiosities + [lead]
        })

        add_clue = random.random() < 0.6
        gender_prefix = "A mulher" if criminal.get("gender") == "F" else "O homem"
        traits = [
            f"{gender_prefix} que você procura esteve aqui e",
            f"Vi uma pessoa de cabelo {criminal.get('hair')} que",
            f"Vi alguém com {criminal.get('feature')} que",
            f"Havia por aqui um viajante que costumava jogar {criminal.get('hobby')} e que",
            f"Alguém assim chegou {criminal.get('vehicle')} e",
            f"Uma pessoa assim estava comentando sobre gostar de {criminal.get('cuisine')} e",
            f"Vi uma pessoa {criminal.get('species')} que"
        ]

        criminal_clue = random.choice(traits) + " " if add_clue else " Um viajante "
        lead_lower = lead[0].lower() + lead[1:]

        dialogue_templates = {
            "biblioteca": [
                f"(O bibliotecário ajeita os óculos) {criminal_clue}requisitou pergaminhos raros que descreviam {lead_lower}.",
                f"(O bibliotecário consulta um registro) Tivemos um visitante interessado em histórias sobre {lead_lower}.",
            ],
            "cartografo": [
                f"(O cartógrafo limpa a tinta dos dedos) {criminal_clue}queria um mapa sobre {lead_lower}.",
                f"(O cartógrafo limpa a tinta dos dedos) Um curioso esteve aqui olhando mapas sobre {lead_lower}.",
            ],
            "centro_cultural": [
                f"(O guia local aponta para um mural) {criminal_clue}passou um longo tempo estudando a representação sobre {lead_lower}.",
                f"(O guia local consulta um folheto) Tivemos um visitante procurando por apresentações sobre {lead_lower}.",
            ],
            "estalagem": [
                f"(O estalajadeiro entrega uma chave) {criminal_clue}alugou um quarto, mas passou a noite escrevendo sobre {lead_lower}.",
                f"(O estalajadeiro limpa uma caneca) Alguém com essa descrição saiu cedo, resmungando algo sobre {lead_lower}.",
            ],
            "estaleiro": [
                f"(O mestre do cais observa as amarras) {criminal_clue}tentou fretar um barco que carregava alguns contêineres com {lead_lower}.",
                f"(O mestre do cais aponta para a água) O sujeito partiu no último barco após fazer perguntas sobre {lead_lower}.",
            ],
            "museu": [
                f"(O curador ajeita uma vitrine) {criminal_clue}demonstrou um interesse acadêmico incomum na exposição sobre {lead_lower}.",
                f"(O curador consulta o catálogo) Lembro-me de um visitante que passou horas examinando artefatos sobre {lead_lower}.",
            ],
            "oficina_gemas": [
                f"(O mestre joalheiro analisa uma pedra) {criminal_clue}trouxe uma joia para avaliar, alegando precisar de fundos para viajar para {lead_lower}.",
                f"(O mestre joalheiro guarda as ferramentas) Um cliente com essas características esteve aqui perguntando sobre {lead_lower}.",
            ],
            "patio_carrocas": [
                f"(O mestre de carga confere uma lista) {criminal_clue}comprou mantimentos para uma viagem, mencionando algo sobre {lead_lower}.",
                f"(O mestre de carga olha o horizonte) Alguém com essas características partiu após questionar sobre {lead_lower}.",
            ],
            "patio_treinamento": [
                f"(O mestre d'armas golpeia o boneco) {criminal_clue}observou os treinos e perguntou sobre {lead_lower}.",
                f"(O mestre d'armas limpa o suor) Alguém perguntou se nossas lâminas seriam eficazes contra {lead_lower}.",
            ],
            "santuario": [
                f"(O sacerdote acende uma vela) {criminal_clue}fez uma oferta aos deuses pedindo proteção e perguntou sobre {lead_lower}.",
                f"(O sacerdote fecha o livro de preces) Tivemos um fiel angustiado que buscava orientação divina sobre {lead_lower}.",
            ],
            "taverna": [
                f"(O taverneiro limpa o balcão) {criminal_clue}esteve aqui e não parava de perguntar sobre {lead_lower}.",
                f"(O taverneiro aponta para uma mesa vazia) Aquele sujeito de quem você falou? Ele passou a noite pesquisando sobre {lead_lower}.",
            ],
            "torre_alta_magia": [
                f"(O arcanista consulta uma esfera) {criminal_clue}contratou um feitiço para recontar sobre {lead_lower}.",
                f"(O arcanista ajusta as vestes) Um visitante assim passou por aqui e quase esqueceu um pergaminho sobre {lead_lower}.",
            ],
        }

        templates = dialogue_templates.get(venue_id, [
            f"(O {role} olha para você) {criminal_clue}demonstrou um interesse incomum sobre {lead_lower}.",
            f"(O {role} faz uma pausa) Me lembro de alguém perguntando sobre o relato de que {lead_lower}.",
        ])

        return https_fn.Response(
            json.dumps({"clue": random.choice(templates), "captured": False}),
            mimetype="application/json", headers=cors_headers
        )

    except Exception as e:
        return https_fn.Response(json.dumps({"error": str(e)}), status=500, headers=cors_headers)


@https_fn.on_request()
def travel(req: https_fn.Request) -> https_fn.Response:
    cors_headers, is_options = handle_cors(req)
    if is_options:
        return cors_headers

    db = firestore.client()
    try:
        data = req.get_json()
        target_city_id = data.get("targetCityId")
        history = data.get("history", [target_city_id])

        session_id = data.get("sessionId")
        session_ref, session = get_valid_session(db, session_id)

        if not session:
            return https_fn.Response(
                json.dumps({"error": "Sessao nao encontrada ou expirada."}),
                status=404, mimetype="application/json", headers=cors_headers
            )

        current_location_before = session.get("current_location")
        current_step = session["current_step"]
        trail = session["trail"]
        venues_per_city = session.get("venues_per_city", {})
        distractors_per_city = session.get("distractors_per_city", {})

        if (current_location_before == trail[current_step]
                and current_step + 1 < len(trail)
                and target_city_id == trail[current_step + 1]):
            current_step += 1

        player_on_trail = (target_city_id == trail[current_step])

        if player_on_trail and target_city_id not in venues_per_city:
            venues_per_city[target_city_id] = random.sample(VENUE_IDS, 3)
        elif not player_on_trail:
            venues_per_city[target_city_id] = []

        if player_on_trail and target_city_id not in distractors_per_city:
            all_city_ids = [d.to_dict()["id"] for d in db.collection("cities").select(["id"]).stream()]
            non_trail_ids = [c for c in all_city_ids if c not in trail]
            distractors_per_city[target_city_id] = random.sample(non_trail_ids, min(4, len(non_trail_ids)))

        update_payload = {
            "current_location": target_city_id,
            "current_step": current_step,
            "venues_per_city": venues_per_city,
        }
        if player_on_trail:
            update_payload["distractors_per_city"] = distractors_per_city

        session_ref.update(update_payload)

        travel_options = _build_travel_options(
            trail_ids=trail,
            current_step=current_step,
            current_location=target_city_id,
            history=history,
            distractors=distractors_per_city.get(target_city_id, []),
        )

        return https_fn.Response(
            json.dumps({
                "cityId": target_city_id,
                "venues": venues_per_city[target_city_id],
                "travelOptions": travel_options,
            }),
            mimetype="application/json", headers=cors_headers
        )

    except Exception as e:
        return https_fn.Response(json.dumps({"error": str(e)}), status=500, headers=cors_headers)


@https_fn.on_request()
def arrest(req: https_fn.Request) -> https_fn.Response:
    """
    Valida o mandado emitido contra o criminoso real da sessao.
    A captura em si e sinalizada pelo endpoint /investigate (captured=True);
    este endpoint e chamado pelo frontend imediatamente apos essa sinalizacao.
    """
    cors_headers, is_options = handle_cors(req)
    if is_options:
        return cors_headers

    db = firestore.client()
    try:
        data = req.get_json()
        session_id = data.get("sessionId")
        session_ref, session = get_valid_session(db, session_id)

        if not session:
            return https_fn.Response(
                json.dumps({"error": "Sessao nao encontrada ou expirada."}),
                status=404, mimetype="application/json", headers=cors_headers
            )

        warrant_id = data.get("warrantId")
        status = "won" if warrant_id == session["criminal_id"] else "wrong_warrant"

        return https_fn.Response(
            json.dumps({"status": status}),
            mimetype="application/json", headers=cors_headers
        )

    except Exception as e:
        return https_fn.Response(json.dumps({"error": str(e)}), status=500, headers=cors_headers)

@https_fn.on_request()
def end_session(req: https_fn.Request) -> https_fn.Response:
    cors_headers, is_options = handle_cors(req)
    if is_options:
        return cors_headers

    db = firestore.client()
    try:
        from datetime import datetime, timezone
        data = req.get_json(silent=True) or {}
        session_id = data.get("sessionId")
        session_ref, session = get_valid_session(db, session_id)

        if not session:
            return https_fn.Response(
                json.dumps({"error": "Sessao nao encontrada."}),
                status=404, mimetype="application/json", headers=cors_headers
            )

        session_ref.update({
            "status": data.get("status", "ongoing"),
            "duration": data.get("duration", ""),
            "end_time": datetime.now(timezone.utc),
        })

        return https_fn.Response(
            json.dumps({"ok": True}),
            mimetype="application/json", headers=cors_headers
        )
    except Exception as e:
        return https_fn.Response(
            json.dumps({"error": str(e)}), status=500, headers=cors_headers
        )