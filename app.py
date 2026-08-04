"""
Backlog - Anjun Brasil
=============================================
App para subir a base de backlog (mesmo modelo cru de 87 colunas do
monitoramento_da_pontualidade_de_pedido) e acompanhar o painel de
pacotes parados: dias sem movimentação e dias desde o recebimento no
ponto, sempre calculados em relação a HOJE (view backlog_atual no banco,
nunca fica desatualizado).

Diferença importante em relação ao app de Pontualidade: aqui o
casamento de colunas é por TEXTO do cabeçalho (não por posição fixa),
porque esse arquivo pode vir com colunas extras no meio (ex: "Dia de
recebimento", "Dias compilados" -- cálculos que o time já faz na mão no
Excel). Colunas duplicadas (ex: "Centro real de chegada" aparece 3x)
são casadas na ordem em que aparecem, igual ao modelo original.

--------------------------------------------------------------------
NOTA (30/07): o filtro "Backlog Ativo" (que exclui pedidos com perda já
confirmada ou que não pertencem à base) foi migrado para a view SQL
`backlog_ativo`, em vez de ser reaplicado em Python a cada query com a
lista MOTIVOS_JA_RESOLVIDOS. Isso evita que outro app/BI que se conecte
nesse Supabase esqueça de aplicar o mesmo corte -- a regra de negócio
agora mora num único lugar (o banco). MOTIVOS_JA_RESOLVIDOS continua
aqui só para exibir a lista pro usuário no st.caption.

NOTA (04/08): adicionada aba "Cobrança IATA" com lista completa de
waybills por ponto/entregador, filtros interativos, indicador de
urgência e export CSV -- voltada para cobrança operacional dos DSPs
(carteira ex-Helson e futuramente qualquer supervisor configurável).

NOTA (04/08 v2): adicionada coluna "Motivo da ocorrência" na aba
Cobrança IATA, incluindo sinalização visual de casos críticos como
roubo, interceptação, fake delivery e perda confirmada.
--------------------------------------------------------------------
"""

import io
import json
import os
import re
from datetime import datetime

import pandas as pd
import psycopg2
import plotly.graph_objects as go
import streamlit as st
from psycopg2.extras import execute_values

# ---------------------------------------------------------------------
# Identidade visual Anjun Brasil
# ---------------------------------------------------------------------
ANJUN_GREEN = "#009946"
ANJUN_GREEN_DARK = "#00753A"
ANJUN_RED = "#E80115"
COR_CRITICO = "#C2410C"
COR_EXTRAVIO = "#C00000"

UFS_REGIONAL = ["AM", "AP", "PA", "RR"]

MOTIVOS_JA_RESOLVIDOS = [
    "Perda confirmada - Aguardando indenização",
    "Perda confirmada-Fake delivery",
    "Pacote não pertence à Base",
]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGO_PATH = os.path.join(BASE_DIR, "assets", "anjun_logo.png")
MAPPING_PATH = os.path.join(BASE_DIR, "config", "column_mapping_base.json")

st.set_page_config(
    page_title="Backlog | Anjun Brasil",
    page_icon=LOGO_PATH,
    layout="wide",
)

st.markdown(
    f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Manrope:wght@600;700;800&family=Inter:wght@400;500;600&display=swap');

    html, body, [class*="css"] {{ font-family: 'Inter', -apple-system, sans-serif; }}
    h1, h2, h3, h4, h5, h6 {{ font-family: 'Manrope', sans-serif !important; letter-spacing: -0.01em; }}

    .block-container {{ padding-top: 2rem; max-width: 1300px; }}

    div[data-testid="stMetric"] {{
        background-color: #F0F7F2;
        border: 1px solid #E0EDE4;
        border-radius: 12px;
        padding: 0.9rem 1.1rem;
        transition: box-shadow 0.15s ease;
    }}
    div[data-testid="stMetric"]:hover {{ box-shadow: 0 4px 14px rgba(0,153,70,0.12); }}
    div[data-testid="stMetricValue"] {{ color: {ANJUN_GREEN_DARK}; font-family: 'Manrope', sans-serif; }}
    div[data-testid="stMetricLabel"] {{ font-weight: 600; color: #3D5245 !important; }}

    .stTabs [data-baseweb="tab"] {{ font-weight: 700; font-size: 1rem; }}
    .stButton > button[kind="primary"] {{
        background-color: {ANJUN_GREEN}; border-color: {ANJUN_GREEN}; font-weight: 600;
    }}
    .stButton > button[kind="primary"]:hover {{
        background-color: {ANJUN_GREEN_DARK}; border-color: {ANJUN_GREEN_DARK};
    }}

    div[data-testid="stVerticalBlockBorderWrapper"] {{
        border-radius: 14px !important;
        border-color: #E7EEE9 !important;
        transition: box-shadow 0.18s ease, transform 0.18s ease, border-color 0.18s ease;
        padding: 0.2rem;
    }}
    div[data-testid="stVerticalBlockBorderWrapper"]:hover {{
        box-shadow: 0 10px 26px rgba(22,36,28,0.08);
        transform: translateY(-2px);
        border-color: #C9DECE !important;
    }}

    .chart-title {{
        font-family: 'Manrope', sans-serif; font-weight: 700; font-size: 0.85rem;
        letter-spacing: 0.02em; color: var(--text-color, #16241C); margin-bottom: 0.4rem;
        opacity: 0.85;
    }}
    .section-title {{
        font-family: 'Manrope', sans-serif; font-weight: 800; font-size: 1.25rem;
        color: {ANJUN_GREEN}; margin: 0.4rem 0 0.8rem 0;
        display: flex; align-items: center; gap: 0.5rem;
    }}

    /* Urgência na aba Cobrança */
    .urgencia-critico  {{ color: #C00000; font-weight: 700; }}
    .urgencia-alto     {{ color: #C2410C; font-weight: 700; }}
    .urgencia-medio    {{ color: #D4A017; font-weight: 600; }}
    .urgencia-baixo    {{ color: #009946; font-weight: 500; }}

    .st-key-dsp_view_bg {{
        background: linear-gradient(180deg, #EAF5EC 0%, #F5FAF6 100%);
        border-radius: 16px;
        padding: 1.2rem 1.2rem 1.6rem 1.2rem;
    }}
    .st-key-dsp_view_bg div[data-testid="stVerticalBlockBorderWrapper"] {{
        background: #FFFFFF;
        border-color: #DCEBE0 !important;
    }}
    .st-key-dsp_view_bg .chart-title {{ color: #16241C !important; opacity: 1; }}
    </style>
    """,
    unsafe_allow_html=True,
)

col_logo, col_title = st.columns([1, 6])
with col_logo:
    st.image(LOGO_PATH, width=140)
with col_title:
    st.markdown(
        """
        <div style="padding-top: 0.6rem;">
            <h1 style="color:#00753A; margin-bottom:0;">Backlog</h1>
            <p style="color:#555; margin-top:0.2rem;">Anjun Brasil &middot; pacotes parados: dias sem movimentação e desde o recebimento</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------
# Conexão com o banco
# ---------------------------------------------------------------------
@st.cache_resource
def get_connection():
    cfg = st.secrets["supabase"]
    conn = psycopg2.connect(
        host=cfg["host"], port=cfg["port"], dbname=cfg["dbname"],
        user=cfg["user"], password=cfg["password"], sslmode="require",
    )
    conn.autocommit = True
    return conn


def run_query(sql: str, params=None) -> pd.DataFrame:
    conn = get_connection()
    try:
        return pd.read_sql(sql, conn, params=params)
    except Exception:
        conn.rollback()
        raise


@st.cache_data
def load_column_mapping():
    with open(MAPPING_PATH, encoding="utf-8") as f:
        return json.load(f)


mapping = load_column_mapping()


# ---------------------------------------------------------------------
# Casamento de colunas por TEXTO
# ---------------------------------------------------------------------
def resolver_colunas(header_row, mapping):
    posicoes = {}
    for idx, h in enumerate(header_row):
        h = "" if h is None else str(h)
        posicoes.setdefault(h, []).append(idx)

    usados = {}
    resolvido = []
    faltando = []
    for m in mapping:
        texto = m["original"]
        n = usados.get(texto, 0)
        lista = posicoes.get(texto, [])
        if n < len(lista):
            resolvido.append((m["slug"], lista[n]))
            usados[texto] = n + 1
        else:
            resolvido.append((m["slug"], None))
            faltando.append(texto)

    todos_esperados = {m["original"] for m in mapping}
    extras = [h for h in header_row if h and str(h) not in todos_esperados]
    return resolvido, faltando, extras


def to_text(v):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, (pd.Timestamp, datetime)):
        return str(v)
    if isinstance(v, pd.Timedelta):
        return str(v)
    return str(v)


def parse_periodo_from_filename(filename):
    m = re.search(r"(\d{4}-\d{2}-\d{2})", filename)
    if m:
        return m.group(1)
    m = re.search(r"(\d{1,2}_\d{1,2}_A_\d{1,2}_\d{1,2})", filename, re.IGNORECASE)
    return m.group(1) if m else filename.rsplit(".", 1)[0]


def insert_backlog(data_df, resolvido, arquivo_origem, periodo, batch_size=1000, progress_cb=None):
    slugs = [s for s, _ in resolvido]
    idxs = [i for _, i in resolvido]
    cols_sql = ["arquivo_origem", "periodo_referencia", "row_num"] + slugs
    set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols_sql if c != "numero_do_waybill")
    set_clause += ", data_importacao = now(), data_snapshot = CURRENT_DATE"
    insert_sql = (
        "INSERT INTO public.backlog (" + ", ".join(cols_sql) + ") VALUES %s "
        "ON CONFLICT (numero_do_waybill) DO UPDATE SET " + set_clause
    )

    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM public.backlog")
        antes = cur.fetchone()[0]

    total = 0
    n = len(data_df)
    with conn.cursor() as cur:
        for start in range(0, n, batch_size):
            chunk = data_df.iloc[start:start + batch_size]
            rows = []
            for i, row in chunk.iterrows():
                vals = [arquivo_origem, periodo, i + 1] + [
                    (to_text(row[idx]) if idx is not None else None) for idx in idxs
                ]
                rows.append(tuple(vals))
            execute_values(cur, insert_sql, rows)
            total += len(rows)
            if progress_cb:
                progress_cb(total / n)

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM public.backlog")
        apos_upsert = cur.fetchone()[0]

    removidos = 0
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM public.backlog WHERE data_snapshot = CURRENT_DATE AND arquivo_origem != %s",
            (arquivo_origem,),
        )
        removidos = cur.rowcount

    novos = apos_upsert - antes
    atualizados = total - novos
    return total, novos, atualizados, removidos


def registrar_snapshot_historico():
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO public.backlog_historico_diario
                (data_snapshot, total, d0_4, d5_13, critico, extravio,
                 media_dias_recebimento, media_dias_sem_movimentacao)
            SELECT
                data_snapshot,
                count(*),
                count(*) FILTER (WHERE faixa_recebimento = '0 a 4 dias'),
                count(*) FILTER (WHERE faixa_recebimento = '05 a 13 dias'),
                count(*) FILTER (WHERE faixa_recebimento = '14 a 20 dias (Crítico)'),
                count(*) FILTER (WHERE faixa_recebimento = 'Mais de 20 (Extravio)'),
                avg(dias_desde_recebimento),
                avg(dias_sem_movimentacao)
            FROM public.backlog_atual
            WHERE estado_do_ponto_de_entrada = ANY(%(uf_regional)s)
            GROUP BY data_snapshot
            ON CONFLICT (data_snapshot) DO UPDATE SET
                total = EXCLUDED.total, d0_4 = EXCLUDED.d0_4, d5_13 = EXCLUDED.d5_13,
                critico = EXCLUDED.critico, extravio = EXCLUDED.extravio,
                media_dias_recebimento = EXCLUDED.media_dias_recebimento,
                media_dias_sem_movimentacao = EXCLUDED.media_dias_sem_movimentacao
        """, {"uf_regional": UFS_REGIONAL})


# ---------------------------------------------------------------------
# Helpers da aba Cobrança
# ---------------------------------------------------------------------
def _urgencia_label(dias):
    """Classifica urgência com base nos dias de atraso."""
    if dias is None or pd.isna(dias):
        return "⚠️ S/ prazo", "urgencia-medio"
    dias = int(dias)
    if dias >= 30:
        return f"🔴 {dias}d — CRÍTICO", "urgencia-critico"
    if dias >= 14:
        return f"🟠 {dias}d — Alto", "urgencia-alto"
    if dias >= 5:
        return f"🟡 {dias}d — Médio", "urgencia-medio"
    if dias >= 0:
        return f"🟢 {dias}d — Baixo", "urgencia-baixo"
    return f"⏳ dentro do prazo ({abs(dias)}d)", "urgencia-baixo"


# Motivos que exigem atenção especial — sinalização visual diferenciada
MOTIVOS_ALERTA = {
    "Pacote roubado":                          "🚨 Pacote roubado",
    "O pacote foi interceptado":               "🚨 Interceptado",
    "Perda confirmada-Fake delivery":          "⚠️ Fake delivery",
    "Perda confirmada - Aguardando indenização": "⚠️ Perda confirmada",
    "Local de area de risco":                  "⚠️ Área de risco",
    "Pacote avariado – Retorno":               "📦 Avariado",
}


def _formatar_motivo(motivo):
    """Retorna o motivo com prefixo de alerta quando aplicável."""
    if not motivo or pd.isna(motivo):
        return "—"
    return MOTIVOS_ALERTA.get(str(motivo).strip(), str(motivo))


def _df_cobranca(supervisor_filtro=None):
    """
    Busca no banco todos os pedidos em backlog do snapshot mais recente,
    filtrados pelos pontos de um supervisor específico (ou todos, se None).
    Retorna DataFrame pronto para exibição, incluindo motivo da ocorrência
    com sinalização visual para casos críticos (roubo, interceptação, etc).
    """
    params = {"uf_regional": UFS_REGIONAL}
    filtro_supervisor = ""
    if supervisor_filtro:
        filtro_supervisor = "AND s.supervisor = ANY(%(supervisores)s)"
        params["supervisores"] = supervisor_filtro

    sql = f"""
        SELECT
            b.ponto_de_entrega                          AS "Ponto (IATA)",
            s.supervisor                                AS "Supervisor",
            b.entregador                                AS "Entregador",
            b.numero_do_waybill                         AS "Waybill",
            b.cidade_do_destinatario                    AS "Cidade",
            b.estado_do_destinatario                    AS "UF",
            b.horario_em_que_deve_ser_entregue          AS "Prazo",
            b.ultimo_data_de_rastreio                   AS "Último rastreio",
            b.ultimo_rastreio                           AS "Último status",
            b.status_do_pacote                          AS "Status do pacote",
            b.motivo_da_ocorrencia                      AS "Motivo (raw)",
            (CURRENT_DATE - TO_DATE(
                LEFT(b.horario_em_que_deve_ser_entregue, 10), 'YYYY-MM-DD'
            ))                                          AS "Dias atraso",
            b.data_snapshot                             AS "Snapshot"
        FROM public.backlog b
        LEFT JOIN public.supervisores s
            ON s.ponto = b.ponto_de_entrega
        WHERE b.data_snapshot = (SELECT MAX(data_snapshot) FROM public.backlog)
          AND b.ponto_de_entrega IS NOT NULL
          {filtro_supervisor}
        ORDER BY b.ponto_de_entrega, "Dias atraso" DESC NULLS LAST
    """
    return run_query(sql, params)


# ─────────────────────────────────────────────────────────────────────
# TABS
# ─────────────────────────────────────────────────────────────────────
tab_upload, tab_painel, tab_dsp, tab_cobranca = st.tabs([
    "Upload da Base",
    "Painel de Backlog",
    "Visão DSP",
    "📋 Cobrança IATA",
])

# ===================== ABA UPLOAD =====================
def render_upload():
    st.subheader("Subir arquivo de backlog")
    st.caption(
        "Aceita o export padrão (mesmo modelo do monitoramento_da_pontualidade_de_pedido). "
        "Colunas extras que o arquivo já trouxer (ex: cálculos manuais no Excel) são "
        "ignoradas automaticamente -- os dias em atraso são recalculados sempre com a data de hoje."
    )
    st.caption(
        "🔒 Protegido contra duplicidade: se um pedido já existir na base, a linha é "
        "atualizada, não duplicada. Pode subir tranquilo mesmo sem ter certeza se aquele "
        "arquivo já foi enviado antes."
    )

    uploaded = st.file_uploader("Arquivo .xlsx", type=["xlsx"])

    if uploaded is not None:
        try:
            raw_df = pd.read_excel(uploaded, sheet_name=0, header=None)
        except Exception as e:
            st.error(f"Não consegui ler o arquivo: {e}")
            return

        header_row = raw_df.iloc[0].tolist()
        data_df = raw_df.iloc[1:].reset_index(drop=True)

        resolvido, faltando, extras = resolver_colunas(header_row, mapping)

        if faltando:
            st.error(
                "Esse arquivo está sem colunas que a tabela `backlog` espera. "
                "Antes de subir, confirma se é o modelo certo (fale com quem mantém o banco)."
            )
            st.write(f"**Faltando ({len(faltando)}):**")
            st.code("\n".join(faltando))
        else:
            st.success(f"Modelo confere: {len(data_df)} linhas, {len(header_row)} colunas no arquivo.")
            if extras:
                with st.expander(f"ℹ️ {len(extras)} coluna(s) extra(s) no arquivo (ignoradas)"):
                    st.caption(
                        "Essas colunas existem no arquivo mas não fazem parte do modelo -- "
                        "provavelmente cálculos que vocês já fazem no Excel. Não são salvas; "
                        "os indicadores equivalentes são recalculados pela view do banco."
                    )
                    st.code("\n".join(extras))

            preview_cols = {slug: (data_df[idx] if idx is not None else pd.Series([None] * len(data_df)))
                             for slug, idx in resolvido[:10]}
            st.dataframe(pd.DataFrame(preview_cols).head(10), use_container_width=True)

            col_a, col_b = st.columns(2)
            with col_a:
                periodo = st.text_input(
                    "Período de referência (data da extração)",
                    value=parse_periodo_from_filename(uploaded.name),
                )
            with col_b:
                st.text_input("Arquivo de origem", value=uploaded.name, disabled=True)

            if st.button("Confirmar e subir para o banco", type="primary"):
                progress = st.progress(0.0, text="Enviando...")

                def _cb(frac):
                    progress.progress(min(frac, 1.0), text=f"Enviando... {frac:.0%}")

                n, novos, atualizados, removidos = insert_backlog(data_df, resolvido, uploaded.name, periodo, progress_cb=_cb)
                registrar_snapshot_historico()
                progress.empty()
                msg = (
                    f"{n} linhas processadas: **{novos} pedidos novos**, "
                    f"**{atualizados} já existentes** atualizados (nenhum duplicado)."
                )
                if removidos:
                    msg += (
                        f" **{removidos} linha(s) de um upload anterior de hoje (outro arquivo) foram removidas** "
                        f"-- o backlog de hoje passa a refletir só esse arquivo."
                    )
                msg += " Snapshot de hoje registrado no histórico."
                st.success(msg)
                st.balloons()
                st.cache_data.clear()

with tab_upload:
    render_upload()

# ===================== ABA PAINEL =====================
with tab_painel:
    st.subheader("Painel de Backlog")

    snap_info = run_query("SELECT max(data_snapshot) AS ultimo FROM public.backlog")
    ultimo_snapshot = snap_info.iloc[0]["ultimo"] if not snap_info.empty else None
    hoje = datetime.now().date()

    if ultimo_snapshot is None:
        st.info("Base ainda vazia -- sobe um arquivo na aba Upload.")
    elif ultimo_snapshot == hoje:
        st.success(f"🟢 Backlog de **hoje** ({hoje:%d/%m/%Y}) — 今天的积压数据。O painel abaixo mostra só a carga mais recente.")
    else:
        dias_atras = (hoje - ultimo_snapshot).days
        st.warning(
            f"⚠️ Os dados são de **{ultimo_snapshot:%d/%m/%Y}** ({dias_atras} dia(s) atrás), não de hoje. "
            f"数据不是今天的，是 {dias_atras} 天前的。Sobe um arquivo novo na aba Upload pra atualizar."
        )

    if ultimo_snapshot is None:
        st.stop()

    modo_backlog = st.radio(
        "Visão", ["Backlog Ativo (padrão)", "Backlog Total (inclui perdas confirmadas)"],
        horizontal=True,
    )
    tabela_fonte = "backlog_ativo" if modo_backlog.startswith("Backlog Ativo") else "backlog_atual"
    if modo_backlog.startswith("Backlog Ativo"):
        st.caption(
            "Exclui pedidos já fechados como perda ou que não pertencem à base "
            "(confirmado com o time em 24/07): " + "; ".join(MOTIVOS_JA_RESOLVIDOS)
        )

    opcoes_uf = UFS_REGIONAL
    opcoes_ponto = run_query("SELECT DISTINCT ponto_de_entrada AS v FROM public.backlog_atual WHERE ponto_de_entrada IS NOT NULL ORDER BY 1")["v"].tolist()
    opcoes_supervisor = run_query("SELECT DISTINCT supervisor AS v FROM public.backlog_atual WHERE supervisor IS NOT NULL ORDER BY 1")["v"].tolist()
    opcoes_status = run_query("SELECT DISTINCT status_do_pacote AS v FROM public.backlog_atual WHERE status_do_pacote IS NOT NULL ORDER BY 1")["v"].tolist()
    opcoes_merchant = run_query("SELECT DISTINCT nome_do_cliente_merchant AS v FROM public.backlog_atual WHERE nome_do_cliente_merchant IS NOT NULL ORDER BY 1")["v"].tolist()
    opcoes_cliente = run_query("SELECT DISTINCT nome_do_cliente AS v FROM public.backlog_atual WHERE nome_do_cliente IS NOT NULL ORDER BY 1")["v"].tolist()

    with st.expander("Filtros / 筛选", expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            f_uf = st.multiselect("UF (do ponto de entrada)", opcoes_uf)
        with c2:
            f_ponto = st.multiselect("Ponto de Entrada / DSP", opcoes_ponto)
        with c3:
            f_supervisor = st.multiselect("Supervisor", opcoes_supervisor)

        c4, c5, c6 = st.columns(3)
        with c4:
            f_status = st.multiselect("Status do Pacote", opcoes_status)
        with c5:
            f_merchant = st.multiselect("Cliente Merchant / 商户名称", opcoes_merchant)
        with c6:
            f_cliente = st.multiselect("Nome do Cliente / 客户名称", opcoes_cliente)

    clauses, params = ["estado_do_ponto_de_entrada = ANY(%(uf_regional)s)"], {"uf_regional": UFS_REGIONAL}
    if f_uf:
        clauses.append("estado_do_ponto_de_entrada = ANY(%(uf)s)")
        params["uf"] = f_uf
    if f_ponto:
        clauses.append("ponto_de_entrada = ANY(%(ponto)s)")
        params["ponto"] = f_ponto
    if f_supervisor:
        clauses.append("supervisor = ANY(%(supervisor)s)")
        params["supervisor"] = f_supervisor
    if f_status:
        clauses.append("status_do_pacote = ANY(%(status)s)")
        params["status"] = f_status
    if f_merchant:
        clauses.append("nome_do_cliente_merchant = ANY(%(merchant)s)")
        params["merchant"] = f_merchant
    if f_cliente:
        clauses.append("nome_do_cliente = ANY(%(cliente)s)")
        params["cliente"] = f_cliente
    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    and_or_where = " AND " if where_sql else "WHERE "

    total = run_query(f"SELECT count(*) AS n FROM public.{tabela_fonte} {where_sql}", params).iloc[0]["n"]

    if total == 0:
        st.warning("Nenhum pacote encontrado com esse filtro.")
    else:
        cor_faixa = {
            "0 a 4 dias": ANJUN_GREEN, "05 a 13 dias": "#D4A017",
            "14 a 20 dias (Crítico)": COR_CRITICO, "Mais de 20 (Extravio)": COR_EXTRAVIO,
        }
        ordem_faixa = list(cor_faixa.keys())

        kpi = run_query(
            f"""SELECT count(*) AS total, avg(dias_sem_movimentacao) AS media_mov,
                       avg(dias_desde_recebimento) AS media_receb,
                       count(*) FILTER (WHERE faixa_recebimento IN ('14 a 20 dias (Crítico)','Mais de 20 (Extravio)')) AS n_atencao
                FROM public.{tabela_fonte} {where_sql}""",
            params,
        ).iloc[0]

        st.markdown('<div class="section-title">📊 Visão Geral / 总览</div>', unsafe_allow_html=True)
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Pacotes em backlog", f"{int(kpi['total']):,}".replace(",", "."))
        k2.metric("Média dias sem movimentação", f"{kpi['media_mov']:.1f}")
        k3.metric("Média dias desde recebimento", f"{kpi['media_receb']:.1f}")
        pct_atencao = 100 * kpi["n_atencao"] / kpi["total"] if kpi["total"] else 0
        k4.metric("Crítico + Extravio", f"{int(kpi['n_atencao']):,}".replace(",", "."), delta=f"{pct_atencao:.1f}% do total", delta_color="inverse")

        st.divider()

        st.markdown('<div class="section-title">📈 Tendência do Backlog / 积压趋势</div>', unsafe_allow_html=True)
        st.caption("Um ponto por dia (calendário, não por upload) -- se subir 2x no mesmo dia, conta como 1 ponto.")
        hist = run_query("SELECT * FROM public.backlog_historico_diario ORDER BY data_snapshot")
        if len(hist) < 2:
            with st.container(border=True):
                st.info(f"Só há 1 snapshot registrado até agora ({hist.iloc[0]['data_snapshot']:%d/%m}, total {int(hist.iloc[0]['total']):,}). "
                        "A tendência aparece a partir do 2º dia de upload.".replace(",", "."))
        else:
            eixo_x = list(hist["data_snapshot"])
            rotulos_x = [d.strftime("%d/%m") for d in eixo_x]
            mostrar_todos_rotulos = len(hist) <= 8

            def _estilo_eixo(fig, altura_extra=0.0):
                fig.update_xaxes(
                    type="date", tickmode="array", tickvals=eixo_x, ticktext=rotulos_x,
                    tickangle=0, hoverformat="%d/%m/%Y", showgrid=False,
                    tickfont=dict(size=12, family="Manrope"),
                )
                fig.update_yaxes(showgrid=True, gridcolor="rgba(0,0,0,0.06)", zeroline=False)
                fig.update_layout(hovermode="x unified")
                return fig

            def _rotulos_pontos(y_vals):
                if mostrar_todos_rotulos:
                    return [f"{int(v):,}".replace(",", ".") for v in y_vals]
                return [f"{int(v):,}".replace(",", ".") if i in (0, len(y_vals) - 1) else ""
                        for i, v in enumerate(y_vals)]

            def _delta_badge(serie, inverso_bom=True):
                if len(serie) < 2 or not serie.iloc[-2]:
                    return ""
                var = (serie.iloc[-1] - serie.iloc[-2]) / serie.iloc[-2] * 100
                melhorou = (var < 0) if inverso_bom else (var > 0)
                cor = ANJUN_GREEN if melhorou else COR_EXTRAVIO
                seta = "▼" if var < 0 else "▲"
                return f'<span style="color:{cor}; font-weight:800; font-size:1.05rem;">{seta} {abs(var):.1f}%</span> <span style="color:#8A968E; font-size:0.8rem;">vs dia anterior</span>'

            col_t1, col_t2 = st.columns(2)

            with col_t1, st.container(border=True):
                st.markdown('<div class="chart-title">🚨 CRÍTICO + EXTRAVIO — ATENÇÃO</div>', unsafe_allow_html=True)
                badge_extravio = _delta_badge(hist["extravio"])
                badge_critico = _delta_badge(hist["critico"])
                st.markdown(
                    f'<div style="display:flex; gap:2rem; margin-bottom:0.6rem;">'
                    f'<div><span style="color:{COR_EXTRAVIO}; font-weight:700; font-size:0.8rem;">● EXTRAVIO</span><br>{badge_extravio}</div>'
                    f'<div><span style="color:{COR_CRITICO}; font-weight:700; font-size:0.8rem;">● CRÍTICO</span><br>{badge_critico}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=eixo_x, y=hist["extravio"], name="Extravio (+20d)",
                    mode="lines+markers+text", line=dict(color=COR_EXTRAVIO, width=4, shape="spline"),
                    marker=dict(size=11, color=COR_EXTRAVIO, line=dict(width=2, color="white")),
                    text=_rotulos_pontos(hist["extravio"]), textposition="top center", cliponaxis=False,
                    textfont=dict(size=13, color=COR_EXTRAVIO, family="Manrope", weight=700),
                    fill="tozeroy", fillcolor="rgba(192,0,0,0.07)",
                    hovertemplate="Extravio: <b>%{y:,}</b><extra></extra>",
                ))
                fig.add_trace(go.Scatter(
                    x=eixo_x, y=hist["critico"], name="Crítico (14-20d)",
                    mode="lines+markers+text", line=dict(color=COR_CRITICO, width=4, shape="spline"),
                    marker=dict(size=11, color=COR_CRITICO, line=dict(width=2, color="white")),
                    text=_rotulos_pontos(hist["critico"]), textposition="bottom center", cliponaxis=False,
                    textfont=dict(size=13, color=COR_CRITICO, family="Manrope", weight=700),
                    fill="tozeroy", fillcolor="rgba(194,65,12,0.07)",
                    hovertemplate="Crítico: <b>%{y:,}</b><extra></extra>",
                ))
                fig.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#6B7A72"), height=380,
                    margin=dict(l=10, r=40, t=50, b=30),
                    legend=dict(orientation="h", yanchor="bottom", y=1.1, font=dict(size=12)),
                    yaxis_title="Pacotes",
                )
                st.plotly_chart(_estilo_eixo(fig), use_container_width=True)

            with col_t2, st.container(border=True):
                st.markdown('<div class="chart-title">🟢 0-4 E 05-13 DIAS — SAUDÁVEL</div>', unsafe_allow_html=True)
                badge_d0_4 = _delta_badge(hist["d0_4"], inverso_bom=False)
                badge_d5_13 = _delta_badge(hist["d5_13"])
                st.markdown(
                    f'<div style="display:flex; gap:2rem; margin-bottom:0.6rem;">'
                    f'<div><span style="color:{ANJUN_GREEN}; font-weight:700; font-size:0.8rem;">● 0 A 4 DIAS</span><br>{badge_d0_4}</div>'
                    f'<div><span style="color:#D4A017; font-weight:700; font-size:0.8rem;">● 05 A 13 DIAS</span><br>{badge_d5_13}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                fig2 = go.Figure()
                fig2.add_trace(go.Scatter(
                    x=eixo_x, y=hist["d0_4"], name="0 a 4 dias",
                    mode="lines+markers+text", line=dict(color=ANJUN_GREEN, width=4, shape="spline"),
                    marker=dict(size=11, color=ANJUN_GREEN, line=dict(width=2, color="white")),
                    text=_rotulos_pontos(hist["d0_4"]), textposition="top center", cliponaxis=False,
                    textfont=dict(size=13, color=ANJUN_GREEN_DARK, family="Manrope", weight=700),
                    fill="tozeroy", fillcolor="rgba(0,153,70,0.07)",
                    hovertemplate="0 a 4 dias: <b>%{y:,}</b><extra></extra>",
                ))
                fig2.add_trace(go.Scatter(
                    x=eixo_x, y=hist["d5_13"], name="05 a 13 dias",
                    mode="lines+markers+text", line=dict(color="#D4A017", width=4, shape="spline"),
                    marker=dict(size=11, color="#D4A017", line=dict(width=2, color="white")),
                    text=_rotulos_pontos(hist["d5_13"]), textposition="bottom center", cliponaxis=False,
                    textfont=dict(size=13, color="#8A6A0F", family="Manrope", weight=700),
                    fill="tozeroy", fillcolor="rgba(212,160,23,0.08)",
                    hovertemplate="05 a 13 dias: <b>%{y:,}</b><extra></extra>",
                ))
                fig2.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#6B7A72"), height=380,
                    margin=dict(l=10, r=40, t=50, b=30),
                    legend=dict(orientation="h", yanchor="bottom", y=1.1, font=dict(size=12)),
                    yaxis_title="Pacotes",
                )
                st.plotly_chart(_estilo_eixo(fig2), use_container_width=True)

        st.divider()

        st.markdown('<div class="section-title">👤 Backlog por Responsável, detalhado por faixa / 按负责人及天数分布</div>', unsafe_allow_html=True)
        df_sf = run_query(
            f"""SELECT COALESCE(supervisor, '(sem responsável)') AS supervisor, faixa_recebimento, count(*) AS n
                FROM public.{tabela_fonte} {where_sql} {and_or_where} faixa_recebimento IS NOT NULL
                GROUP BY supervisor, faixa_recebimento""",
            params,
        )
        with st.container(border=True):
            if not df_sf.empty:
                ordem_sup = df_sf.groupby("supervisor")["n"].sum().sort_values(ascending=False).index.tolist()
                fig = go.Figure()
                for faixa in ordem_faixa:
                    sub = df_sf[df_sf["faixa_recebimento"] == faixa].set_index("supervisor").reindex(ordem_sup).fillna(0)
                    fig.add_trace(go.Bar(name=faixa, x=ordem_sup, y=sub["n"], marker_color=cor_faixa[faixa]))
                fig.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#6B7A72"),
                    barmode="stack", height=360, margin=dict(l=10, r=10, t=10, b=10),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02))
                st.plotly_chart(fig, use_container_width=True)
        st.caption("Colunas: `supervisor` (via `ponto_de_entrada`) × `faixa_recebimento`.")

        st.divider()

        st.markdown('<div class="section-title">📐 Distribuições / 分布</div>', unsafe_allow_html=True)
        colA, colB = st.columns(2)
        with colA, st.container(border=True):
            st.markdown('<div class="chart-title">DIAS DESDE O RECEBIMENTO</div>', unsafe_allow_html=True)
            df_faixa = run_query(
                f"""SELECT faixa_recebimento, count(*) AS n FROM public.{tabela_fonte} {where_sql}
                    {and_or_where} faixa_recebimento IS NOT NULL GROUP BY faixa_recebimento""",
                params,
            )
            df_faixa["ordem"] = df_faixa["faixa_recebimento"].apply(lambda x: ordem_faixa.index(x) if x in ordem_faixa else 99)
            df_faixa = df_faixa.sort_values("ordem")
            pct_faixa = 100 * df_faixa["n"] / df_faixa["n"].sum()
            rotulos_faixa = [f"{n:,}".replace(",", ".") + f"  ({p:.1f}%)" for n, p in zip(df_faixa["n"], pct_faixa)]
            fig = go.Figure(go.Bar(
                x=df_faixa["n"], y=df_faixa["faixa_recebimento"], orientation="h",
                marker_color=[cor_faixa.get(f, "#999") for f in df_faixa["faixa_recebimento"]],
                text=rotulos_faixa, textposition="outside", cliponaxis=False,
            ))
            fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#6B7A72"),
                height=280, margin=dict(l=10, r=95, t=10, b=10), showlegend=False,
                xaxis=dict(visible=False),
                yaxis=dict(autorange="reversed", automargin=True))
            st.plotly_chart(fig, use_container_width=True)

        with colB, st.container(border=True):
            st.markdown('<div class="chart-title">DIAS SEM MOVIMENTAÇÃO</div>', unsafe_allow_html=True)
            st.caption("Diferente da faixa ao lado: aqui é *quando foi o último rastreio*, não *quando chegou*.")
            df_mov = run_query(
                f"""SELECT
                      CASE WHEN dias_sem_movimentacao <= 2 THEN '0 a 2 dias'
                           WHEN dias_sem_movimentacao <= 5 THEN '3 a 5 dias'
                           WHEN dias_sem_movimentacao <= 10 THEN '6 a 10 dias'
                           ELSE 'Mais de 10 dias' END AS faixa_mov,
                      count(*) AS n
                    FROM public.{tabela_fonte} {where_sql}
                    {and_or_where} dias_sem_movimentacao IS NOT NULL GROUP BY faixa_mov""",
                params,
            )
            ordem_mov = ["0 a 2 dias", "3 a 5 dias", "6 a 10 dias", "Mais de 10 dias"]
            cores_mov = [ANJUN_GREEN, "#D4A017", COR_CRITICO, COR_EXTRAVIO]
            df_mov["ordem"] = df_mov["faixa_mov"].apply(lambda x: ordem_mov.index(x) if x in ordem_mov else 99)
            df_mov = df_mov.sort_values("ordem")
            fig = go.Figure(go.Bar(
                x=df_mov["faixa_mov"], y=df_mov["n"],
                marker_color=[cores_mov[ordem_mov.index(f)] for f in df_mov["faixa_mov"]],
                text=[f"{v:,}".replace(",", ".") for v in df_mov["n"]], textposition="outside", cliponaxis=False,
            ))
            fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#6B7A72"),
                height=320, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="Pacotes")
            st.plotly_chart(fig, use_container_width=True)

        st.divider()

        st.markdown('<div class="section-title">🗺️ Geografia e Rede / 地理与网点</div>', unsafe_allow_html=True)
        colC, colD = st.columns(2)
        with colC, st.container(border=True):
            st.markdown('<div class="chart-title">POR ESTADO</div>', unsafe_allow_html=True)
            df_uf = run_query(
                f"""SELECT estado_do_ponto_de_entrada AS grupo, count(*) AS n FROM public.{tabela_fonte} {where_sql}
                    {and_or_where} estado_do_ponto_de_entrada IS NOT NULL GROUP BY grupo ORDER BY n DESC""",
                params,
            )
            fig = go.Figure(go.Bar(x=df_uf["grupo"], y=df_uf["n"], marker_color=ANJUN_GREEN_DARK,
                                    text=[f"{v:,}".replace(",", ".") for v in df_uf["n"]], textposition="outside", cliponaxis=False))
            fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#6B7A72"), height=340, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig, use_container_width=True)

        with colD, st.container(border=True):
            st.markdown('<div class="chart-title">POR PONTO DE ENTRADA / DSP (TOP 15)</div>', unsafe_allow_html=True)
            df_dsp = run_query(
                f"""SELECT ponto_de_entrada AS grupo, count(*) AS n FROM public.{tabela_fonte} {where_sql}
                    {and_or_where} ponto_de_entrada IS NOT NULL GROUP BY grupo ORDER BY n DESC LIMIT 15""",
                params,
            )
            fig = go.Figure(go.Bar(x=df_dsp["n"], y=df_dsp["grupo"], orientation="h", marker_color=COR_CRITICO,
                                    text=[f"{v:,}".replace(",", ".") for v in df_dsp["n"]], textposition="outside", cliponaxis=False))
            fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#6B7A72"), height=340, margin=dict(l=10, r=65, t=10, b=10))
            fig.update_yaxes(autorange="reversed", automargin=True)
            st.plotly_chart(fig, use_container_width=True)

        st.divider()

        st.markdown('<div class="section-title">🏷️ Cliente e Motivo / 客户与原因</div>', unsafe_allow_html=True)
        colE, colF = st.columns(2)
        with colE, st.container(border=True):
            st.markdown('<div class="chart-title">POR CLIENTE MERCHANT (TOP 15)</div>', unsafe_allow_html=True)
            df_merchant = run_query(
                f"""SELECT nome_do_cliente_merchant AS grupo, count(*) AS n FROM public.{tabela_fonte} {where_sql}
                    {and_or_where} nome_do_cliente_merchant IS NOT NULL GROUP BY grupo ORDER BY n DESC LIMIT 15""",
                params,
            )
            fig = go.Figure(go.Bar(x=df_merchant["n"], y=df_merchant["grupo"], orientation="h", marker_color=ANJUN_GREEN,
                                    text=[f"{v:,}".replace(",", ".") for v in df_merchant["n"]], textposition="outside", cliponaxis=False))
            fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#6B7A72"), height=380, margin=dict(l=10, r=65, t=10, b=10))
            fig.update_yaxes(autorange="reversed", automargin=True)
            st.plotly_chart(fig, use_container_width=True)

        with colF, st.container(border=True):
            st.markdown('<div class="chart-title">MOTIVO DA OCORRÊNCIA — SÓ CRÍTICO + EXTRAVIO</div>', unsafe_allow_html=True)
            df_motivo = run_query(
                f"""SELECT motivo_da_ocorrencia AS grupo, count(*) AS n FROM public.{tabela_fonte} {where_sql}
                    {and_or_where} faixa_recebimento IN ('14 a 20 dias (Crítico)','Mais de 20 (Extravio)')
                    AND motivo_da_ocorrencia IS NOT NULL
                    GROUP BY grupo ORDER BY n DESC LIMIT 10""",
                params,
            )
            if df_motivo.empty:
                st.info("Nenhuma ocorrência registrada nesse recorte.")
            else:
                fig = go.Figure(go.Bar(x=df_motivo["n"], y=df_motivo["grupo"], orientation="h", marker_color=COR_EXTRAVIO,
                                        text=[f"{v:,}".replace(",", ".") for v in df_motivo["n"]], textposition="outside", cliponaxis=False))
                fig.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#6B7A72"), height=380, margin=dict(l=10, r=65, t=10, b=10))
                fig.update_yaxes(autorange="reversed", automargin=True)
                st.plotly_chart(fig, use_container_width=True)

        st.divider()

        st.markdown('<div class="section-title">📦 Status do Pacote / 运单状态</div>', unsafe_allow_html=True)
        df_status = run_query(
            f"""SELECT status_do_pacote AS grupo, count(*) AS n FROM public.{tabela_fonte} {where_sql}
                {and_or_where} status_do_pacote IS NOT NULL GROUP BY grupo ORDER BY n DESC""",
            params,
        )
        with st.container(border=True):
            fig = go.Figure(go.Bar(x=df_status["n"], y=df_status["grupo"], orientation="h", marker_color=ANJUN_GREEN_DARK,
                                    text=[f"{v:,}".replace(",", ".") for v in df_status["n"]], textposition="outside", cliponaxis=False))
            fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#6B7A72"), height=280, margin=dict(l=10, r=65, t=10, b=10))
            fig.update_yaxes(autorange="reversed", automargin=True)
            st.plotly_chart(fig, use_container_width=True)

        st.divider()

        st.markdown('<div class="section-title">🚨 Pacotes mais críticos (ordenado do mais parado pro menos) / 最紧急包裹</div>', unsafe_allow_html=True)
        criticos = run_query(
            f"""SELECT numero_do_waybill AS "Waybill", ponto_de_entrada AS "Ponto de Entrada",
                       supervisor AS "Supervisor", status_do_pacote AS "Status",
                       nome_do_cliente_merchant AS "Cliente Merchant",
                       motivo_da_ocorrencia AS "Motivo",
                       dias_sem_movimentacao AS "Dias sem mov.",
                       dias_desde_recebimento AS "Dias desde receb.",
                       faixa_recebimento AS "Faixa"
                FROM public.{tabela_fonte} {where_sql}
                ORDER BY dias_desde_recebimento DESC NULLS LAST LIMIT 200""",
            params,
        )
        st.dataframe(criticos, use_container_width=True, hide_index=True)

# ===================== ABA VISÃO DSP =====================
with tab_dsp:
    snap_info2 = run_query("SELECT max(data_snapshot) AS ultimo FROM public.backlog")
    ultimo2 = snap_info2.iloc[0]["ultimo"] if not snap_info2.empty else None

    if ultimo2 is None:
        st.info("Base ainda vazia -- sobe um arquivo na aba Upload.")
    else:
        with st.container(key="dsp_view_bg"):
            st.markdown(
                f"""
                <div style="position:relative; overflow:hidden; background:linear-gradient(135deg, {ANJUN_GREEN_DARK} 0%, {ANJUN_GREEN} 100%);
                            border-radius:16px; padding:1.3rem 1.8rem; margin-bottom:1.3rem;
                            box-shadow:0 10px 24px rgba(0,77,38,0.22);">
                    <div style="position:absolute; inset:0; background-image:radial-gradient(rgba(255,255,255,0.12) 1px, transparent 1px);
                                background-size:16px 16px; opacity:0.5;"></div>
                    <div style="position:relative; z-index:1; display:flex; align-items:center; gap:1rem;">
                        <span style="background:white; border-radius:10px; padding:0.35rem 0.7rem; font-weight:800; color:{ANJUN_GREEN}; font-family:'Manrope',sans-serif;">Anjun</span>
                        <span style="color:white; font-size:1.5rem; font-weight:800; font-family:'Manrope',sans-serif;">
                            Backlog Diário {ultimo2:%d/%m} | 每日积压{ultimo2:%m}月{ultimo2:%d}日
                        </span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.caption(f"Regional: {', '.join(UFS_REGIONAL)} -- apenas os estados que a Anjun responde nessa operação.")

            modo_dsp = st.radio(
                "Visão", ["Backlog Ativo (padrão)", "Backlog Total (inclui perdas confirmadas)"],
                horizontal=True, key="modo_dsp",
            )
            tabela_dsp = "backlog_ativo" if modo_dsp.startswith("Backlog Ativo") else "backlog_atual"

            cor_faixa = {
                "0 a 4 dias": ANJUN_GREEN, "05 a 13 dias": "#D4A017",
                "14 a 20 dias (Crítico)": COR_CRITICO, "Mais de 20 (Extravio)": COR_EXTRAVIO,
            }
            ordem_faixa = list(cor_faixa.keys())
            uf_params = {"uf_regional": UFS_REGIONAL}
            total_dsp = run_query(
                f"SELECT count(*) AS n FROM public.{tabela_dsp} WHERE estado_do_ponto_de_entrada = ANY(%(uf_regional)s)",
                uf_params,
            ).iloc[0]["n"]

            r1c1, r1c2, r1c3, r1c4 = st.columns([1.2, 0.9, 1.1, 0.9])

            with r1c1, st.container(border=True):
                st.markdown('<div class="chart-title">RESPONSÁVEL / 负责人</div>', unsafe_allow_html=True)
                df = run_query(
                    f"""SELECT COALESCE(supervisor,'(sem resp.)') AS grupo, count(*) AS n
                       FROM public.{tabela_dsp} WHERE estado_do_ponto_de_entrada = ANY(%(uf_regional)s)
                       GROUP BY grupo ORDER BY n DESC""", uf_params,
                )
                fig = go.Figure(go.Bar(x=df["n"], y=df["grupo"], orientation="h", marker_color=COR_EXTRAVIO,
                                        text=[f"{v:,}".replace(",", ".") for v in df["n"]], textposition="outside", cliponaxis=False))
                fig.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#6B7A72"), height=260, margin=dict(l=10, r=30, t=5, b=5))
                fig.update_yaxes(autorange="reversed", automargin=True)
                st.plotly_chart(fig, use_container_width=True)

            with r1c2, st.container(border=True):
                st.markdown('<div class="chart-title">BACKLOG / 积压</div>', unsafe_allow_html=True)
                fig = go.Figure(go.Indicator(
                    mode="number", value=total_dsp,
                    number={"valueformat": ",", "font": {"size": 42, "color": COR_EXTRAVIO}},
                ))
                fig.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#6B7A72"), height=260, margin=dict(l=10, r=10, t=30, b=5))
                st.plotly_chart(fig, use_container_width=True)

            with r1c3, st.container(border=True):
                st.markdown('<div class="chart-title">DIAS DE RECEBIMENTO / 收到后几天</div>', unsafe_allow_html=True)
                df = run_query(
                    f"""SELECT faixa_recebimento, count(*) AS n FROM public.{tabela_dsp}
                       WHERE estado_do_ponto_de_entrada = ANY(%(uf_regional)s) AND faixa_recebimento IS NOT NULL
                       GROUP BY faixa_recebimento""", uf_params,
                )
                df["ordem"] = df["faixa_recebimento"].apply(lambda x: ordem_faixa.index(x) if x in ordem_faixa else 99)
                df = df.sort_values("ordem")
                pct_dsp = 100 * df["n"] / df["n"].sum()
                rotulos_dsp = [f"{n:,}".replace(",", ".") + f" ({p:.1f}%)" for n, p in zip(df["n"], pct_dsp)]
                fig = go.Figure(go.Bar(
                    x=df["n"], y=df["faixa_recebimento"], orientation="h",
                    marker_color=[cor_faixa.get(f, "#999") for f in df["faixa_recebimento"]],
                    text=rotulos_dsp, textposition="outside", cliponaxis=False,
                    textfont=dict(size=10),
                ))
                fig.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#6B7A72", size=10), height=280, margin=dict(l=10, r=75, t=5, b=5),
                    showlegend=False, xaxis=dict(visible=False),
                    yaxis=dict(autorange="reversed", automargin=True))
                st.plotly_chart(fig, use_container_width=True)

            with r1c4, st.container(border=True):
                st.markdown('<div class="chart-title">POR ESTADO / 各州</div>', unsafe_allow_html=True)
                df = run_query(
                    f"""SELECT estado_do_ponto_de_entrada AS grupo, count(*) AS n FROM public.{tabela_dsp}
                       WHERE estado_do_ponto_de_entrada = ANY(%(uf_regional)s) GROUP BY grupo ORDER BY n DESC""",
                    uf_params,
                )
                fig = go.Figure(go.Bar(x=df["grupo"], y=df["n"], marker_color=COR_EXTRAVIO,
                                        text=[f"{v:,}".replace(",", ".") for v in df["n"]], textposition="outside", cliponaxis=False))
                fig.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#6B7A72"), height=260, margin=dict(l=10, r=10, t=5, b=5))
                st.plotly_chart(fig, use_container_width=True)

            st.write("")
            r2c1, r2c2 = st.columns(2)
            with r2c1, st.container(border=True):
                st.markdown('<div class="chart-title">BACKLOG POR DSP / 每个交付点的积压情况</div>', unsafe_allow_html=True)
                df = run_query(
                    f"""SELECT ponto_de_entrada AS grupo, count(*) AS n FROM public.{tabela_dsp}
                       WHERE estado_do_ponto_de_entrada = ANY(%(uf_regional)s) AND ponto_de_entrada IS NOT NULL
                       GROUP BY grupo ORDER BY n DESC LIMIT 15""", uf_params,
                )
                fig = go.Figure(go.Bar(x=df["n"], y=df["grupo"], orientation="h", marker_color=COR_CRITICO,
                                        text=[f"{v:,}".replace(",", ".") for v in df["n"]], textposition="outside", cliponaxis=False))
                fig.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#6B7A72"), height=460, margin=dict(l=10, r=65, t=5, b=5))
                fig.update_yaxes(autorange="reversed", automargin=True)
                st.plotly_chart(fig, use_container_width=True)

            with r2c2, st.container(border=True):
                st.markdown('<div class="chart-title">BACKLOG POR ENTREGADOR / 按派送员</div>', unsafe_allow_html=True)
                df = run_query(
                    f"""SELECT entregador AS grupo, count(*) AS n FROM public.{tabela_dsp}
                       WHERE estado_do_ponto_de_entrada = ANY(%(uf_regional)s) AND entregador IS NOT NULL
                       GROUP BY grupo ORDER BY n DESC LIMIT 15""", uf_params,
                )
                fig = go.Figure(go.Bar(x=df["n"], y=df["grupo"], orientation="h", marker_color=ANJUN_GREEN,
                                        text=[f"{v:,}".replace(",", ".") for v in df["n"]], textposition="outside", cliponaxis=False))
                fig.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#6B7A72"), height=460, margin=dict(l=10, r=65, t=5, b=5))
                fig.update_yaxes(autorange="reversed", automargin=True)
                st.plotly_chart(fig, use_container_width=True)

            st.caption(
                f"Coluna: `entregador` (派送员) — ranking de quem tem mais backlog. "
                f"Todos os gráficos acima já filtram só a regional ({', '.join(UFS_REGIONAL)})."
            )

# ===================== ABA COBRANÇA IATA =====================
with tab_cobranca:
    st.subheader("📋 Cobrança IATA — Lista de Waybills por Entregador")
    st.caption(
        "Lista operacional para cobrança diária dos DSPs. Mostra todos os pedidos em backlog "
        "com ponto de entrega mapeado, agrupados por ponto e entregador, ordenados pelo maior atraso. "
        "Filtre por supervisor para ver só a sua carteira."
    )

    # ── Filtros da aba Cobrança ──────────────────────────────────────
    opcoes_sup_cob = run_query(
        "SELECT DISTINCT supervisor FROM public.supervisores WHERE supervisor IS NOT NULL ORDER BY 1"
    )["supervisor"].tolist()

    col_f1, col_f2, col_f3 = st.columns([2, 2, 1])
    with col_f1:
        f_sup_cob = st.multiselect(
            "Filtrar por Supervisor",
            opcoes_sup_cob,
            default=[],
            key="cob_supervisor",
            help="Deixe em branco para ver todos os supervisores.",
        )
    with col_f2:
        f_min_dias = st.number_input(
            "Atraso mínimo (dias)",
            min_value=0, max_value=365, value=0, step=1,
            key="cob_min_dias",
            help="Filtra só pedidos com X dias ou mais de atraso.",
        )
    with col_f3:
        apenas_anomalia = st.checkbox(
            "Só 'Pedido com anomalia'",
            value=False,
            key="cob_anomalia",
        )

    # ── Carrega dados ────────────────────────────────────────────────
    df_cob = _df_cobranca(f_sup_cob if f_sup_cob else None)

    if df_cob.empty:
        st.warning("Nenhum pedido encontrado com ponto de entrega mapeado no snapshot mais recente.")
        st.stop()

    # Converte tipos
    df_cob["Dias atraso"] = pd.to_numeric(df_cob["Dias atraso"], errors="coerce")

    # Aplica filtros adicionais
    if f_min_dias > 0:
        df_cob = df_cob[df_cob["Dias atraso"].fillna(0) >= f_min_dias]
    if apenas_anomalia:
        df_cob = df_cob[df_cob["Status do pacote"].str.contains("anomalia", case=False, na=False)]

    if df_cob.empty:
        st.warning("Nenhum pedido com esses filtros.")
        st.stop()

    # Adiciona coluna de urgência (texto, para exibição)
    df_cob["Urgência"] = df_cob["Dias atraso"].apply(lambda d: _urgencia_label(d)[0])

    # Formata motivo com prefixo de alerta
    df_cob["Motivo da ocorrência"] = df_cob["Motivo (raw)"].apply(_formatar_motivo)

    # ── KPIs rápidos ─────────────────────────────────────────────────
    total_cob = len(df_cob)
    criticos_cob = len(df_cob[df_cob["Dias atraso"].fillna(0) >= 30])
    pontos_cob = df_cob["Ponto (IATA)"].nunique()
    entregadores_cob = df_cob["Entregador"].nunique()
    alertas_cob = df_cob["Motivo (raw)"].isin(MOTIVOS_ALERTA.keys()).sum()

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Total de pedidos", total_cob)
    k2.metric("Críticos (30+ dias)", criticos_cob,
              delta=f"{100*criticos_cob/total_cob:.0f}% do total" if total_cob else "0%",
              delta_color="inverse")
    k3.metric("Pontos (IATAs)", pontos_cob)
    k4.metric("Entregadores distintos", entregadores_cob)
    k5.metric("🚨 Roubos / Perdas / Risco", alertas_cob,
              delta="requer ação imediata" if alertas_cob > 0 else "nenhum",
              delta_color="inverse" if alertas_cob > 0 else "normal")

    st.divider()

    # ── Resumo por ponto ─────────────────────────────────────────────
    st.markdown('<div class="section-title">📍 Resumo por Ponto (IATA)</div>', unsafe_allow_html=True)

    resumo_ponto = (
        df_cob.groupby("Ponto (IATA)")
        .agg(
            Supervisor=("Supervisor", "first"),
            Total=("Waybill", "count"),
            Criticos=("Dias atraso", lambda x: (x.fillna(0) >= 30).sum()),
            Mais_antigo=("Dias atraso", lambda x: x.max()),
        )
        .reset_index()
        .sort_values("Total", ascending=False)
        .rename(columns={"Criticos": "Críticos (30+d)", "Mais_antigo": "Mais antigo (dias)"})
    )
    st.dataframe(resumo_ponto, use_container_width=True, hide_index=True)

    st.divider()

    # ── Lista completa agrupada por ponto → entregador ───────────────
    st.markdown('<div class="section-title">🎯 Waybills por Entregador (para cobrança)</div>', unsafe_allow_html=True)
    st.caption("Expandido por ponto. Clique no cabeçalho da coluna para reordenar.")

    pontos_disponiveis = sorted(df_cob["Ponto (IATA)"].dropna().unique())

    for ponto in pontos_disponiveis:
        df_ponto = df_cob[df_cob["Ponto (IATA)"] == ponto].copy()
        supervisor_ponto = df_ponto["Supervisor"].dropna().iloc[0] if not df_ponto["Supervisor"].dropna().empty else "—"
        total_ponto = len(df_ponto)
        critico_ponto = len(df_ponto[df_ponto["Dias atraso"].fillna(0) >= 14])
        mais_antigo = int(df_ponto["Dias atraso"].max()) if not df_ponto["Dias atraso"].isna().all() else "—"

        label_critico = f"🔴" if critico_ponto == total_ponto else f"🟠" if critico_ponto > total_ponto * 0.5 else "🟡"

        with st.expander(
            f"{label_critico} **{ponto}** — {total_ponto} pedido(s) · {critico_ponto} crítico(s) · mais antigo: {mais_antigo}d · Supervisor: {supervisor_ponto}",
            expanded=(total_ponto >= 3),
        ):
            # Colunas relevantes para cobrança
            colunas_exibir = [
                "Entregador", "Waybill", "Urgência",
                "Dias atraso", "Motivo da ocorrência", "Prazo",
                "Último rastreio", "Cidade", "UF", "Status do pacote",
            ]
            df_show = df_ponto[colunas_exibir].sort_values("Dias atraso", ascending=False, na_position="last")

            # Aviso se houver casos críticos de motivo dentro do ponto
            alertas_ponto = df_ponto["Motivo (raw)"].isin(MOTIVOS_ALERTA.keys()).sum()
            if alertas_ponto > 0:
                motivos_encontrados = (
                    df_ponto[df_ponto["Motivo (raw)"].isin(MOTIVOS_ALERTA.keys())]
                    ["Motivo da ocorrência"].value_counts()
                )
                resumo = ", ".join(f"{m} ({n}x)" for m, n in motivos_encontrados.items())
                st.warning(f"🚨 **{alertas_ponto} pedido(s) com motivo crítico:** {resumo}")

            st.dataframe(
                df_show,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Waybill": st.column_config.TextColumn("Waybill", width="medium"),
                    "Urgência": st.column_config.TextColumn("Urgência", width="medium"),
                    "Motivo da ocorrência": st.column_config.TextColumn("Motivo da ocorrência", width="large"),
                    "Dias atraso": st.column_config.NumberColumn("Dias atraso", format="%d"),
                    "Prazo": st.column_config.DatetimeColumn("Prazo", format="DD/MM/YYYY"),
                    "Último rastreio": st.column_config.DatetimeColumn("Último rastreio", format="DD/MM HH:mm"),
                },
            )

            # Export individual por ponto
            csv_ponto = df_show.to_csv(index=False).encode("utf-8")
            st.download_button(
                label=f"⬇️ Baixar CSV — {ponto}",
                data=csv_ponto,
                file_name=f"cobranca_{ponto}_{datetime.now():%Y%m%d}.csv",
                mime="text/csv",
                key=f"dl_{ponto}",
            )

    st.divider()

    # ── Export completo ───────────────────────────────────────────────
    st.markdown('<div class="section-title">⬇️ Export Completo</div>', unsafe_allow_html=True)
    col_exp1, col_exp2 = st.columns(2)

    with col_exp1:
        csv_completo = df_cob.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="⬇️ Baixar CSV completo (todos os pontos)",
            data=csv_completo,
            file_name=f"cobranca_todos_pontos_{datetime.now():%Y%m%d}.csv",
            mime="text/csv",
            key="dl_completo",
        )

    with col_exp2:
        # Export por entregador: um bloco de texto copiável para colar no WhatsApp/Telegram
        linhas_msg = []
        for ponto, grp_ponto in df_cob.groupby("Ponto (IATA)"):
            linhas_msg.append(f"\n📍 *{ponto}*")
            for entregador, grp_ent in grp_ponto.groupby("Entregador"):
                waybills = grp_ent["Waybill"].tolist()
                dias_max = grp_ent["Dias atraso"].max()
                dias_str = f"{int(dias_max)}d" if not pd.isna(dias_max) else "s/ prazo"
                linhas_msg.append(f"  👤 {entregador} ({len(waybills)} pedido(s) · max {dias_str})")
                for _, row in grp_ent.iterrows():
                    motivo_raw = row.get("Motivo (raw)", "")
                    eh_alerta = str(motivo_raw).strip() in MOTIVOS_ALERTA
                    motivo_fmt = MOTIVOS_ALERTA.get(str(motivo_raw).strip(), "") if eh_alerta else ""
                    sufixo = f" ← {motivo_fmt}" if motivo_fmt else ""
                    linhas_msg.append(f"    • {row['Waybill']}{sufixo}")
        texto_msg = "\n".join(linhas_msg)

        st.download_button(
            label="📱 Baixar lista .txt (para WhatsApp/grupos)",
            data=texto_msg.encode("utf-8"),
            file_name=f"cobranca_whatsapp_{datetime.now():%Y%m%d}.txt",
            mime="text/plain",
            key="dl_whatsapp",
        )
        st.caption("Formato pronto para colar no grupo do DSP: ponto → entregador → waybills.")