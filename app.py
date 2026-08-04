"""
Backlog - Anjun Brasil
Lógica da aba de cobrança espelha a aba DIN da planilha:
todos os pedidos por DSP × faixa de dias no ponto (tempo_de_inbound).
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

ANJUN_GREEN      = "#009946"
ANJUN_GREEN_DARK = "#00753A"
ANJUN_RED        = "#E80115"
COR_CRITICO      = "#C2410C"
COR_EXTRAVIO     = "#C00000"

UFS_REGIONAL = ["AM", "AP", "PA", "RR"]

MOTIVOS_JA_RESOLVIDOS = [
    "Perda confirmada - Aguardando indenização",
    "Perda confirmada-Fake delivery",
    "Pacote não pertence à Base",
    "Pacote avariado – Retorno",
    "Pacote roubado",
]

MOTIVOS_EXCLUIR_COBRANCA = [
    "Perda confirmada-Fake delivery",
    "Perda confirmada - Aguardando indenização",
    "Pacote avariado – Retorno",
    "Pacote roubado",
]

MOTIVOS_ALERTA = {
    "O pacote foi interceptado":               "🚨 Interceptado",
    "Local de area de risco":                  "⚠️ Área de risco",
    "Cliente ausente":                         "🔄 Cliente ausente",
    "Endereço de destinário errado":           "🔄 Endereço errado",
    "Endereço fora da rota":                   "🔄 Fora da rota",
    "Recusado pelo cliente":                   "🔄 Recusado",
    "Endereço fechado - greve/paralização/férias coletivas/banlanço": "🔄 End. fechado",
    "Veículo quebrado":                        "🔄 Veículo quebrado",
    "Entrega atrasada pelas condições climáticas": "🔄 Clima",
    "Feriado local e reentrega em breve":      "🔄 Feriado",
}

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
LOGO_PATH   = os.path.join(BASE_DIR, "assets", "anjun_logo.png")
MAPPING_PATH = os.path.join(BASE_DIR, "config", "column_mapping_base.json")

st.set_page_config(page_title="Backlog | Anjun Brasil", page_icon=LOGO_PATH, layout="wide")

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Manrope:wght@600;700;800&family=Inter:wght@400;500;600&display=swap');
html, body, [class*="css"] {{ font-family: 'Inter', -apple-system, sans-serif; }}
h1, h2, h3, h4, h5, h6 {{ font-family: 'Manrope', sans-serif !important; letter-spacing: -0.01em; }}
.block-container {{ padding-top: 2rem; max-width: 1300px; }}
div[data-testid="stMetric"] {{ background-color: #F0F7F2; border: 1px solid #E0EDE4; border-radius: 12px; padding: 0.9rem 1.1rem; transition: box-shadow 0.15s ease; }}
div[data-testid="stMetric"]:hover {{ box-shadow: 0 4px 14px rgba(0,153,70,0.12); }}
div[data-testid="stMetricValue"] {{ color: {ANJUN_GREEN_DARK}; font-family: 'Manrope', sans-serif; }}
div[data-testid="stMetricLabel"] {{ font-weight: 600; color: #3D5245 !important; }}
.stTabs [data-baseweb="tab"] {{ font-weight: 700; font-size: 1rem; }}
.stButton > button[kind="primary"] {{ background-color: {ANJUN_GREEN}; border-color: {ANJUN_GREEN}; font-weight: 600; }}
.stButton > button[kind="primary"]:hover {{ background-color: {ANJUN_GREEN_DARK}; border-color: {ANJUN_GREEN_DARK}; }}
div[data-testid="stVerticalBlockBorderWrapper"] {{ border-radius: 14px !important; border-color: #E7EEE9 !important; transition: box-shadow 0.18s ease, transform 0.18s ease, border-color 0.18s ease; padding: 0.2rem; }}
div[data-testid="stVerticalBlockBorderWrapper"]:hover {{ box-shadow: 0 10px 26px rgba(22,36,28,0.08); transform: translateY(-2px); border-color: #C9DECE !important; }}
.chart-title {{ font-family: 'Manrope', sans-serif; font-weight: 700; font-size: 0.85rem; letter-spacing: 0.02em; color: var(--text-color, #16241C); margin-bottom: 0.4rem; opacity: 0.85; }}
.section-title {{ font-family: 'Manrope', sans-serif; font-weight: 800; font-size: 1.25rem; color: {ANJUN_GREEN}; margin: 0.4rem 0 0.8rem 0; display: flex; align-items: center; gap: 0.5rem; }}
.st-key-dsp_view_bg {{ background: linear-gradient(180deg, #EAF5EC 0%, #F5FAF6 100%); border-radius: 16px; padding: 1.2rem 1.2rem 1.6rem 1.2rem; }}
.st-key-dsp_view_bg div[data-testid="stVerticalBlockBorderWrapper"] {{ background: #FFFFFF; border-color: #DCEBE0 !important; }}
.st-key-dsp_view_bg .chart-title {{ color: #16241C !important; opacity: 1; }}
</style>
""", unsafe_allow_html=True)

col_logo, col_title = st.columns([1, 6])
with col_logo:
    st.image(LOGO_PATH, width=140)
with col_title:
    st.markdown("""<div style="padding-top:0.6rem;"><h1 style="color:#00753A;margin-bottom:0;">Backlog</h1>
    <p style="color:#555;margin-top:0.2rem;">Anjun Brasil · pacotes parados: dias sem movimentação e desde o recebimento</p></div>""",
    unsafe_allow_html=True)


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
        effective_params = params if params else None
        return pd.read_sql(sql, conn, params=effective_params)
    except Exception:
        conn.rollback()
        raise


@st.cache_data
def load_column_mapping():
    with open(MAPPING_PATH, encoding="utf-8") as f:
        return json.load(f)


mapping = load_column_mapping()


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
    idxs  = [i for _, i in resolvido]
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


def _urgencia_label(dias):
    if dias is None or pd.isna(dias):
        return "⚠️ S/ prazo", "urgencia-medio"
    dias = int(dias)
    if dias >= 30: return f"🔴 {dias}d — CRÍTICO", "urgencia-critico"
    if dias >= 14: return f"🟠 {dias}d — Alto", "urgencia-alto"
    if dias >= 5:  return f"🟡 {dias}d — Médio", "urgencia-medio"
    if dias >= 0:  return f"🟢 {dias}d — Baixo", "urgencia-baixo"
    return f"⏳ dentro do prazo ({abs(dias)}d)", "urgencia-baixo"


def _formatar_motivo(motivo):
    if not motivo or pd.isna(motivo):
        return "—"
    return MOTIVOS_ALERTA.get(str(motivo).strip(), str(motivo))


def _df_din(supervisor_filtro=None):
    """
    Query única que espelha a aba DIN: todos os pedidos por DSP com faixa
    calculada pelo tempo_de_inbound_no_ponto (dias no DSP), não pelo prazo.
    A separação com/sem entregador é feita em Python, como filtro adicional.
    """
    filtro_sup = ""
    if supervisor_filtro:
        lista = ", ".join("'" + s.replace("'", "''") + "'" for s in supervisor_filtro)
        filtro_sup = f"AND s.supervisor IN ({lista})"

    sql = f"""
        SELECT
            b.ponto_de_entrada                 AS "Ponto (IATA)",
            s.supervisor                       AS "Supervisor",
            b.status_do_pacote                 AS "Status",
            b.entregador                       AS "Entregador",
            b.numero_do_waybill                AS "Waybill",
            b.cidade_do_destinatario           AS "Cidade",
            b.estado_do_destinatario           AS "UF",
            b.horario_em_que_deve_ser_entregue AS "Prazo",
            b.ultimo_data_de_rastreio          AS "Ultimo rastreio",
            b.ultimo_rastreio                  AS "Ultimo status",
            b.motivo_da_ocorrencia             AS "Motivo (raw)",
            (CURRENT_DATE - TO_DATE(
                LEFT(b.horario_em_que_deve_ser_entregue, 10), 'YYYY-MM-DD'
            ))                                 AS "Dias atraso",
            CASE
                WHEN b.tempo_de_inbound_no_ponto IS NULL
                     OR b.tempo_de_inbound_no_ponto LIKE '--%'
                THEN NULL
                ELSE (CURRENT_DATE - TO_DATE(
                        LEFT(b.tempo_de_inbound_no_ponto, 10), 'YYYY-MM-DD'
                     ))
            END                                AS "Dias no DSP",
            CASE
                WHEN b.tempo_de_inbound_no_ponto IS NULL
                     OR b.tempo_de_inbound_no_ponto LIKE '--%'
                     THEN 'Sem data'
                WHEN (CURRENT_DATE - TO_DATE(LEFT(b.tempo_de_inbound_no_ponto,10),'YYYY-MM-DD')) <= 4
                     THEN '0 a 4 dias'
                WHEN (CURRENT_DATE - TO_DATE(LEFT(b.tempo_de_inbound_no_ponto,10),'YYYY-MM-DD')) <= 13
                     THEN '05 a 13 dias'
                WHEN (CURRENT_DATE - TO_DATE(LEFT(b.tempo_de_inbound_no_ponto,10),'YYYY-MM-DD')) <= 20
                     THEN '14 a 20 dias (Crítico)'
                ELSE 'Mais de 20 (Extravio)'
            END                                AS "Faixa"
        FROM public.backlog b
        LEFT JOIN public.supervisores s ON s.ponto = b.ponto_de_entrada
        WHERE b.data_snapshot = (SELECT MAX(data_snapshot) FROM public.backlog)
          AND b.ponto_de_entrada IS NOT NULL
          {filtro_sup}
        ORDER BY b.ponto_de_entrada, "Dias no DSP" DESC NULLS LAST
    """
    conn = get_connection()
    try:
        return pd.read_sql(sql, conn)
    except Exception:
        conn.rollback()
        raise


tab_upload, tab_painel, tab_dsp, tab_cobranca = st.tabs([
    "Upload da Base", "Painel de Backlog", "Visão DSP", "📋 Cobrança IATA",
])


# ===================== ABA UPLOAD =====================
def render_upload():
    st.subheader("Subir arquivo de backlog")
    st.caption("Aceita o export padrão. Colunas extras ignoradas automaticamente.")
    st.caption("🔒 Protegido contra duplicidade: pedido existente é atualizado, nunca duplicado.")
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
            st.error("Arquivo sem colunas esperadas. Confira o modelo.")
            st.code("\n".join(faltando))
        else:
            st.success(f"Modelo confere: {len(data_df)} linhas, {len(header_row)} colunas.")
            if extras:
                with st.expander(f"ℹ️ {len(extras)} coluna(s) extra(s) ignoradas"):
                    st.code("\n".join(extras))
            preview_cols = {slug: (data_df[idx] if idx is not None else pd.Series([None]*len(data_df)))
                            for slug, idx in resolvido[:10]}
            st.dataframe(pd.DataFrame(preview_cols).head(10), use_container_width=True)
            col_a, col_b = st.columns(2)
            with col_a:
                periodo = st.text_input("Período de referência", value=parse_periodo_from_filename(uploaded.name))
            with col_b:
                st.text_input("Arquivo de origem", value=uploaded.name, disabled=True)
            if st.button("Confirmar e subir para o banco", type="primary"):
                progress = st.progress(0.0, text="Enviando...")
                def _cb(frac): progress.progress(min(frac, 1.0), text=f"Enviando... {frac:.0%}")
                n, novos, atualizados, removidos = insert_backlog(data_df, resolvido, uploaded.name, periodo, progress_cb=_cb)
                registrar_snapshot_historico()
                progress.empty()
                msg = f"{n} linhas: **{novos} novos**, **{atualizados} atualizados**."
                if removidos:
                    msg += f" **{removidos} linha(s) de upload anterior removidas**."
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
        st.info("Base ainda vazia — sobe um arquivo na aba Upload.")
    elif ultimo_snapshot == hoje:
        st.success(f"🟢 Backlog de **hoje** ({hoje:%d/%m/%Y}).")
    else:
        dias_atras = (hoje - ultimo_snapshot).days
        st.warning(f"⚠️ Dados de **{ultimo_snapshot:%d/%m/%Y}** ({dias_atras} dia(s) atrás). Sobe um arquivo novo.")
    if ultimo_snapshot is None:
        st.stop()

    modo_backlog = st.radio("Visão", ["Backlog Ativo (padrão)", "Backlog Total (inclui perdas confirmadas)"], horizontal=True)
    tabela_fonte = "backlog_ativo" if modo_backlog.startswith("Backlog Ativo") else "backlog_atual"
    if modo_backlog.startswith("Backlog Ativo"):
        st.caption("Exclui: " + "; ".join(MOTIVOS_JA_RESOLVIDOS))

    opcoes_uf         = UFS_REGIONAL
    opcoes_ponto      = run_query("SELECT DISTINCT ponto_de_entrada AS v FROM public.backlog_atual WHERE ponto_de_entrada IS NOT NULL ORDER BY 1")["v"].tolist()
    opcoes_supervisor = run_query("SELECT DISTINCT supervisor AS v FROM public.backlog_atual WHERE supervisor IS NOT NULL ORDER BY 1")["v"].tolist()
    opcoes_status     = run_query("SELECT DISTINCT status_do_pacote AS v FROM public.backlog_atual WHERE status_do_pacote IS NOT NULL ORDER BY 1")["v"].tolist()
    opcoes_merchant   = run_query("SELECT DISTINCT nome_do_cliente_merchant AS v FROM public.backlog_atual WHERE nome_do_cliente_merchant IS NOT NULL ORDER BY 1")["v"].tolist()
    opcoes_cliente    = run_query("SELECT DISTINCT nome_do_cliente AS v FROM public.backlog_atual WHERE nome_do_cliente IS NOT NULL ORDER BY 1")["v"].tolist()

    with st.expander("Filtros", expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1: f_uf         = st.multiselect("UF", opcoes_uf)
        with c2: f_ponto      = st.multiselect("Ponto / DSP", opcoes_ponto)
        with c3: f_supervisor = st.multiselect("Supervisor", opcoes_supervisor)
        c4, c5, c6 = st.columns(3)
        with c4: f_status   = st.multiselect("Status", opcoes_status)
        with c5: f_merchant = st.multiselect("Cliente Merchant", opcoes_merchant)
        with c6: f_cliente  = st.multiselect("Nome do Cliente", opcoes_cliente)

    clauses, params = ["estado_do_ponto_de_entrada = ANY(%(uf_regional)s)"], {"uf_regional": UFS_REGIONAL}
    if f_uf:         clauses.append("estado_do_ponto_de_entrada = ANY(%(uf)s)");         params["uf"] = f_uf
    if f_ponto:      clauses.append("ponto_de_entrada = ANY(%(ponto)s)");                params["ponto"] = f_ponto
    if f_supervisor: clauses.append("supervisor = ANY(%(supervisor)s)");                 params["supervisor"] = f_supervisor
    if f_status:     clauses.append("status_do_pacote = ANY(%(status)s)");               params["status"] = f_status
    if f_merchant:   clauses.append("nome_do_cliente_merchant = ANY(%(merchant)s)");     params["merchant"] = f_merchant
    if f_cliente:    clauses.append("nome_do_cliente = ANY(%(cliente)s)");               params["cliente"] = f_cliente
    where_sql    = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    and_or_where = " AND " if where_sql else "WHERE "

    total = run_query(f"SELECT count(*) AS n FROM public.{tabela_fonte} {where_sql}", params).iloc[0]["n"]
    if total == 0:
        st.warning("Nenhum pacote encontrado.")
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
                FROM public.{tabela_fonte} {where_sql}""", params).iloc[0]
        st.markdown('<div class="section-title">📊 Visão Geral</div>', unsafe_allow_html=True)
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Pacotes em backlog", f"{int(kpi['total']):,}".replace(",", "."))
        k2.metric("Média dias sem movimentação", f"{kpi['media_mov']:.1f}")
        k3.metric("Média dias desde recebimento", f"{kpi['media_receb']:.1f}")
        pct = 100 * kpi["n_atencao"] / kpi["total"] if kpi["total"] else 0
        k4.metric("Crítico + Extravio", f"{int(kpi['n_atencao']):,}".replace(",", "."),
                  delta=f"{pct:.1f}% do total", delta_color="inverse")
        st.divider()

        st.markdown('<div class="section-title">📈 Tendência</div>', unsafe_allow_html=True)
        hist = run_query("SELECT * FROM public.backlog_historico_diario ORDER BY data_snapshot")
        if len(hist) < 2:
            st.info("Tendência disponível a partir do 2º upload.")
        else:
            eixo_x = list(hist["data_snapshot"])
            rotulos_x = [d.strftime("%d/%m") for d in eixo_x]
            mostrar_todos = len(hist) <= 8
            def _rot(y):
                if mostrar_todos: return [f"{int(v):,}".replace(",", ".") for v in y]
                return [f"{int(v):,}".replace(",", ".") if i in (0, len(y)-1) else "" for i, v in enumerate(y)]
            def _ex(fig):
                fig.update_xaxes(type="date", tickmode="array", tickvals=eixo_x, ticktext=rotulos_x,
                                 tickangle=0, showgrid=False, tickfont=dict(size=12, family="Manrope"))
                fig.update_yaxes(showgrid=True, gridcolor="rgba(0,0,0,0.06)", zeroline=False)
                fig.update_layout(hovermode="x unified")
                return fig
            col_t1, col_t2 = st.columns(2)
            with col_t1, st.container(border=True):
                st.markdown('<div class="chart-title">🚨 CRÍTICO + EXTRAVIO</div>', unsafe_allow_html=True)
                fig = go.Figure()
                fig.add_trace(go.Scatter(x=eixo_x, y=hist["extravio"], name="Extravio",
                    mode="lines+markers+text", line=dict(color=COR_EXTRAVIO, width=4, shape="spline"),
                    marker=dict(size=11, color=COR_EXTRAVIO, line=dict(width=2, color="white")),
                    text=_rot(hist["extravio"]), textposition="top center", cliponaxis=False,
                    textfont=dict(size=13, color=COR_EXTRAVIO, family="Manrope", weight=700),
                    fill="tozeroy", fillcolor="rgba(192,0,0,0.07)"))
                fig.add_trace(go.Scatter(x=eixo_x, y=hist["critico"], name="Crítico",
                    mode="lines+markers+text", line=dict(color=COR_CRITICO, width=4, shape="spline"),
                    marker=dict(size=11, color=COR_CRITICO, line=dict(width=2, color="white")),
                    text=_rot(hist["critico"]), textposition="bottom center", cliponaxis=False,
                    textfont=dict(size=13, color=COR_CRITICO, family="Manrope", weight=700),
                    fill="tozeroy", fillcolor="rgba(194,65,12,0.07)"))
                fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#6B7A72"), height=380, margin=dict(l=10, r=40, t=50, b=30),
                    legend=dict(orientation="h", yanchor="bottom", y=1.1), yaxis_title="Pacotes")
                st.plotly_chart(_ex(fig), use_container_width=True)
            with col_t2, st.container(border=True):
                st.markdown('<div class="chart-title">🟢 0-4 E 05-13 DIAS</div>', unsafe_allow_html=True)
                fig2 = go.Figure()
                fig2.add_trace(go.Scatter(x=eixo_x, y=hist["d0_4"], name="0 a 4 dias",
                    mode="lines+markers+text", line=dict(color=ANJUN_GREEN, width=4, shape="spline"),
                    marker=dict(size=11, color=ANJUN_GREEN, line=dict(width=2, color="white")),
                    text=_rot(hist["d0_4"]), textposition="top center", cliponaxis=False,
                    textfont=dict(size=13, color=ANJUN_GREEN_DARK, family="Manrope", weight=700),
                    fill="tozeroy", fillcolor="rgba(0,153,70,0.07)"))
                fig2.add_trace(go.Scatter(x=eixo_x, y=hist["d5_13"], name="05 a 13 dias",
                    mode="lines+markers+text", line=dict(color="#D4A017", width=4, shape="spline"),
                    marker=dict(size=11, color="#D4A017", line=dict(width=2, color="white")),
                    text=_rot(hist["d5_13"]), textposition="bottom center", cliponaxis=False,
                    textfont=dict(size=13, color="#8A6A0F", family="Manrope", weight=700),
                    fill="tozeroy", fillcolor="rgba(212,160,23,0.08)"))
                fig2.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#6B7A72"), height=380, margin=dict(l=10, r=40, t=50, b=30),
                    legend=dict(orientation="h", yanchor="bottom", y=1.1), yaxis_title="Pacotes")
                st.plotly_chart(_ex(fig2), use_container_width=True)
        st.divider()

        st.markdown('<div class="section-title">👤 Backlog por Responsável</div>', unsafe_allow_html=True)
        df_sf = run_query(
            f"""SELECT COALESCE(supervisor,'(sem responsável)') AS supervisor, faixa_recebimento, count(*) AS n
                FROM public.{tabela_fonte} {where_sql} {and_or_where} faixa_recebimento IS NOT NULL
                GROUP BY supervisor, faixa_recebimento""", params)
        with st.container(border=True):
            if not df_sf.empty:
                ordem_sup = df_sf.groupby("supervisor")["n"].sum().sort_values(ascending=False).index.tolist()
                fig = go.Figure()
                for faixa in ordem_faixa:
                    sub = df_sf[df_sf["faixa_recebimento"] == faixa].set_index("supervisor").reindex(ordem_sup).fillna(0)
                    fig.add_trace(go.Bar(name=faixa, x=ordem_sup, y=sub["n"], marker_color=cor_faixa[faixa]))
                fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#6B7A72"), barmode="stack", height=360, margin=dict(l=10, r=10, t=10, b=10),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02))
                st.plotly_chart(fig, use_container_width=True)
        st.divider()

        st.markdown('<div class="section-title">📐 Distribuições</div>', unsafe_allow_html=True)
        colA, colB = st.columns(2)
        with colA, st.container(border=True):
            st.markdown('<div class="chart-title">DIAS DESDE O RECEBIMENTO</div>', unsafe_allow_html=True)
            df_faixa = run_query(
                f"""SELECT faixa_recebimento, count(*) AS n FROM public.{tabela_fonte} {where_sql}
                    {and_or_where} faixa_recebimento IS NOT NULL GROUP BY faixa_recebimento""", params)
            df_faixa["ordem"] = df_faixa["faixa_recebimento"].apply(lambda x: ordem_faixa.index(x) if x in ordem_faixa else 99)
            df_faixa = df_faixa.sort_values("ordem")
            pct_f = 100 * df_faixa["n"] / df_faixa["n"].sum()
            fig = go.Figure(go.Bar(x=df_faixa["n"], y=df_faixa["faixa_recebimento"], orientation="h",
                marker_color=[cor_faixa.get(f, "#999") for f in df_faixa["faixa_recebimento"]],
                text=[f"{n:,}".replace(",",".") + f"  ({p:.1f}%)" for n, p in zip(df_faixa["n"], pct_f)],
                textposition="outside", cliponaxis=False))
            fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#6B7A72"), height=280, margin=dict(l=10, r=95, t=10, b=10),
                showlegend=False, xaxis=dict(visible=False), yaxis=dict(autorange="reversed", automargin=True))
            st.plotly_chart(fig, use_container_width=True)
        with colB, st.container(border=True):
            st.markdown('<div class="chart-title">DIAS SEM MOVIMENTAÇÃO</div>', unsafe_allow_html=True)
            df_mov = run_query(
                f"""SELECT CASE WHEN dias_sem_movimentacao<=2 THEN '0 a 2 dias'
                           WHEN dias_sem_movimentacao<=5 THEN '3 a 5 dias'
                           WHEN dias_sem_movimentacao<=10 THEN '6 a 10 dias'
                           ELSE 'Mais de 10 dias' END AS faixa_mov, count(*) AS n
                    FROM public.{tabela_fonte} {where_sql}
                    {and_or_where} dias_sem_movimentacao IS NOT NULL GROUP BY faixa_mov""", params)
            ordem_mov = ["0 a 2 dias","3 a 5 dias","6 a 10 dias","Mais de 10 dias"]
            cores_mov = [ANJUN_GREEN, "#D4A017", COR_CRITICO, COR_EXTRAVIO]
            df_mov["ordem"] = df_mov["faixa_mov"].apply(lambda x: ordem_mov.index(x) if x in ordem_mov else 99)
            df_mov = df_mov.sort_values("ordem")
            fig = go.Figure(go.Bar(x=df_mov["faixa_mov"], y=df_mov["n"],
                marker_color=[cores_mov[ordem_mov.index(f)] for f in df_mov["faixa_mov"]],
                text=[f"{v:,}".replace(",",".") for v in df_mov["n"]], textposition="outside", cliponaxis=False))
            fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#6B7A72"), height=320, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="Pacotes")
            st.plotly_chart(fig, use_container_width=True)
        st.divider()

        st.markdown('<div class="section-title">🗺️ Geografia e Rede</div>', unsafe_allow_html=True)
        colC, colD = st.columns(2)
        with colC, st.container(border=True):
            st.markdown('<div class="chart-title">POR ESTADO</div>', unsafe_allow_html=True)
            df_uf = run_query(
                f"""SELECT estado_do_ponto_de_entrada AS grupo, count(*) AS n FROM public.{tabela_fonte} {where_sql}
                    {and_or_where} estado_do_ponto_de_entrada IS NOT NULL GROUP BY grupo ORDER BY n DESC""", params)
            fig = go.Figure(go.Bar(x=df_uf["grupo"], y=df_uf["n"], marker_color=ANJUN_GREEN_DARK,
                text=[f"{v:,}".replace(",",".") for v in df_uf["n"]], textposition="outside", cliponaxis=False))
            fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#6B7A72"), height=340, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig, use_container_width=True)
        with colD, st.container(border=True):
            st.markdown('<div class="chart-title">POR PONTO / DSP (TOP 15)</div>', unsafe_allow_html=True)
            df_dsp_p = run_query(
                f"""SELECT ponto_de_entrada AS grupo, count(*) AS n FROM public.{tabela_fonte} {where_sql}
                    {and_or_where} ponto_de_entrada IS NOT NULL GROUP BY grupo ORDER BY n DESC LIMIT 15""", params)
            fig = go.Figure(go.Bar(x=df_dsp_p["n"], y=df_dsp_p["grupo"], orientation="h", marker_color=COR_CRITICO,
                text=[f"{v:,}".replace(",",".") for v in df_dsp_p["n"]], textposition="outside", cliponaxis=False))
            fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#6B7A72"), height=340, margin=dict(l=10, r=65, t=10, b=10))
            fig.update_yaxes(autorange="reversed", automargin=True)
            st.plotly_chart(fig, use_container_width=True)
        st.divider()

        st.markdown('<div class="section-title">🏷️ Cliente e Motivo</div>', unsafe_allow_html=True)
        colE, colF = st.columns(2)
        with colE, st.container(border=True):
            st.markdown('<div class="chart-title">POR CLIENTE MERCHANT (TOP 15)</div>', unsafe_allow_html=True)
            df_mer = run_query(
                f"""SELECT nome_do_cliente_merchant AS grupo, count(*) AS n FROM public.{tabela_fonte} {where_sql}
                    {and_or_where} nome_do_cliente_merchant IS NOT NULL GROUP BY grupo ORDER BY n DESC LIMIT 15""", params)
            fig = go.Figure(go.Bar(x=df_mer["n"], y=df_mer["grupo"], orientation="h", marker_color=ANJUN_GREEN,
                text=[f"{v:,}".replace(",",".") for v in df_mer["n"]], textposition="outside", cliponaxis=False))
            fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#6B7A72"), height=380, margin=dict(l=10, r=65, t=10, b=10))
            fig.update_yaxes(autorange="reversed", automargin=True)
            st.plotly_chart(fig, use_container_width=True)
        with colF, st.container(border=True):
            st.markdown('<div class="chart-title">MOTIVO — SÓ CRÍTICO + EXTRAVIO</div>', unsafe_allow_html=True)
            df_mot = run_query(
                f"""SELECT motivo_da_ocorrencia AS grupo, count(*) AS n FROM public.{tabela_fonte} {where_sql}
                    {and_or_where} faixa_recebimento IN ('14 a 20 dias (Crítico)','Mais de 20 (Extravio)')
                    AND motivo_da_ocorrencia IS NOT NULL GROUP BY grupo ORDER BY n DESC LIMIT 10""", params)
            if df_mot.empty:
                st.info("Nenhuma ocorrência nesse recorte.")
            else:
                fig = go.Figure(go.Bar(x=df_mot["n"], y=df_mot["grupo"], orientation="h", marker_color=COR_EXTRAVIO,
                    text=[f"{v:,}".replace(",",".") for v in df_mot["n"]], textposition="outside", cliponaxis=False))
                fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#6B7A72"), height=380, margin=dict(l=10, r=65, t=10, b=10))
                fig.update_yaxes(autorange="reversed", automargin=True)
                st.plotly_chart(fig, use_container_width=True)
        st.divider()

        st.markdown('<div class="section-title">📦 Status do Pacote</div>', unsafe_allow_html=True)
        df_status_g = run_query(
            f"""SELECT status_do_pacote AS grupo, count(*) AS n FROM public.{tabela_fonte} {where_sql}
                {and_or_where} status_do_pacote IS NOT NULL GROUP BY grupo ORDER BY n DESC""", params)
        with st.container(border=True):
            fig = go.Figure(go.Bar(x=df_status_g["n"], y=df_status_g["grupo"], orientation="h", marker_color=ANJUN_GREEN_DARK,
                text=[f"{v:,}".replace(",",".") for v in df_status_g["n"]], textposition="outside", cliponaxis=False))
            fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#6B7A72"), height=280, margin=dict(l=10, r=65, t=10, b=10))
            fig.update_yaxes(autorange="reversed", automargin=True)
            st.plotly_chart(fig, use_container_width=True)
        st.divider()

        st.markdown('<div class="section-title">🚨 Pacotes mais críticos</div>', unsafe_allow_html=True)
        criticos = run_query(
            f"""SELECT numero_do_waybill AS "Waybill", ponto_de_entrada AS "Ponto",
                       supervisor AS "Supervisor", status_do_pacote AS "Status",
                       nome_do_cliente_merchant AS "Merchant", motivo_da_ocorrencia AS "Motivo",
                       dias_sem_movimentacao AS "Dias s/ mov", dias_desde_recebimento AS "Dias receb",
                       faixa_recebimento AS "Faixa"
                FROM public.{tabela_fonte} {where_sql}
                ORDER BY dias_desde_recebimento DESC NULLS LAST LIMIT 200""", params)
        st.dataframe(criticos, use_container_width=True, hide_index=True)


# ===================== ABA VISÃO DSP =====================
with tab_dsp:
    snap_info2 = run_query("SELECT max(data_snapshot) AS ultimo FROM public.backlog")
    ultimo2 = snap_info2.iloc[0]["ultimo"] if not snap_info2.empty else None
    if ultimo2 is None:
        st.info("Base ainda vazia.")
    else:
        with st.container(key="dsp_view_bg"):
            st.markdown(f"""<div style="position:relative;overflow:hidden;background:linear-gradient(135deg,{ANJUN_GREEN_DARK} 0%,{ANJUN_GREEN} 100%);
                border-radius:16px;padding:1.3rem 1.8rem;margin-bottom:1.3rem;box-shadow:0 10px 24px rgba(0,77,38,0.22);">
                <div style="position:absolute;inset:0;background-image:radial-gradient(rgba(255,255,255,0.12) 1px,transparent 1px);
                background-size:16px 16px;opacity:0.5;"></div>
                <div style="position:relative;z-index:1;display:flex;align-items:center;gap:1rem;">
                <span style="background:white;border-radius:10px;padding:0.35rem 0.7rem;font-weight:800;color:{ANJUN_GREEN};font-family:'Manrope',sans-serif;">Anjun</span>
                <span style="color:white;font-size:1.5rem;font-weight:800;font-family:'Manrope',sans-serif;">Backlog Diário {ultimo2:%d/%m}</span>
                </div></div>""", unsafe_allow_html=True)
            modo_dsp = st.radio("Visão", ["Backlog Ativo (padrão)", "Backlog Total"], horizontal=True, key="modo_dsp")
            tabela_dsp = "backlog_ativo" if modo_dsp.startswith("Backlog Ativo") else "backlog_atual"
            cor_faixa_d = {"0 a 4 dias": ANJUN_GREEN, "05 a 13 dias": "#D4A017",
                           "14 a 20 dias (Crítico)": COR_CRITICO, "Mais de 20 (Extravio)": COR_EXTRAVIO}
            ordem_faixa_d = list(cor_faixa_d.keys())
            uf_params = {"uf_regional": UFS_REGIONAL}
            total_dsp = run_query(
                f"SELECT count(*) AS n FROM public.{tabela_dsp} WHERE estado_do_ponto_de_entrada = ANY(%(uf_regional)s)", uf_params).iloc[0]["n"]
            r1c1, r1c2, r1c3, r1c4 = st.columns([1.2, 0.9, 1.1, 0.9])
            with r1c1, st.container(border=True):
                st.markdown('<div class="chart-title">RESPONSÁVEL</div>', unsafe_allow_html=True)
                df = run_query(f"""SELECT COALESCE(supervisor,'(sem resp.)') AS grupo, count(*) AS n
                   FROM public.{tabela_dsp} WHERE estado_do_ponto_de_entrada = ANY(%(uf_regional)s)
                   GROUP BY grupo ORDER BY n DESC""", uf_params)
                fig = go.Figure(go.Bar(x=df["n"], y=df["grupo"], orientation="h", marker_color=COR_EXTRAVIO,
                    text=[f"{v:,}".replace(",",".") for v in df["n"]], textposition="outside", cliponaxis=False))
                fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#6B7A72"), height=260, margin=dict(l=10, r=30, t=5, b=5))
                fig.update_yaxes(autorange="reversed", automargin=True)
                st.plotly_chart(fig, use_container_width=True)
            with r1c2, st.container(border=True):
                st.markdown('<div class="chart-title">BACKLOG TOTAL</div>', unsafe_allow_html=True)
                fig = go.Figure(go.Indicator(mode="number", value=total_dsp,
                    number={"valueformat": ",", "font": {"size": 42, "color": COR_EXTRAVIO}}))
                fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#6B7A72"), height=260, margin=dict(l=10, r=10, t=30, b=5))
                st.plotly_chart(fig, use_container_width=True)
            with r1c3, st.container(border=True):
                st.markdown('<div class="chart-title">DIAS DE RECEBIMENTO</div>', unsafe_allow_html=True)
                df = run_query(f"""SELECT faixa_recebimento, count(*) AS n FROM public.{tabela_dsp}
                   WHERE estado_do_ponto_de_entrada = ANY(%(uf_regional)s) AND faixa_recebimento IS NOT NULL
                   GROUP BY faixa_recebimento""", uf_params)
                df["ordem"] = df["faixa_recebimento"].apply(lambda x: ordem_faixa_d.index(x) if x in ordem_faixa_d else 99)
                df = df.sort_values("ordem")
                pct_d = 100 * df["n"] / df["n"].sum()
                fig = go.Figure(go.Bar(x=df["n"], y=df["faixa_recebimento"], orientation="h",
                    marker_color=[cor_faixa_d.get(f,"#999") for f in df["faixa_recebimento"]],
                    text=[f"{n:,}".replace(",",".") + f" ({p:.1f}%)" for n,p in zip(df["n"],pct_d)],
                    textposition="outside", cliponaxis=False, textfont=dict(size=10)))
                fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#6B7A72",size=10), height=280, margin=dict(l=10, r=75, t=5, b=5),
                    showlegend=False, xaxis=dict(visible=False), yaxis=dict(autorange="reversed", automargin=True))
                st.plotly_chart(fig, use_container_width=True)
            with r1c4, st.container(border=True):
                st.markdown('<div class="chart-title">POR ESTADO</div>', unsafe_allow_html=True)
                df = run_query(f"""SELECT estado_do_ponto_de_entrada AS grupo, count(*) AS n FROM public.{tabela_dsp}
                   WHERE estado_do_ponto_de_entrada = ANY(%(uf_regional)s) GROUP BY grupo ORDER BY n DESC""", uf_params)
                fig = go.Figure(go.Bar(x=df["grupo"], y=df["n"], marker_color=COR_EXTRAVIO,
                    text=[f"{v:,}".replace(",",".") for v in df["n"]], textposition="outside", cliponaxis=False))
                fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#6B7A72"), height=260, margin=dict(l=10, r=10, t=5, b=5))
                st.plotly_chart(fig, use_container_width=True)
            st.write("")
            r2c1, r2c2 = st.columns(2)
            with r2c1, st.container(border=True):
                st.markdown('<div class="chart-title">BACKLOG POR DSP</div>', unsafe_allow_html=True)
                df = run_query(f"""SELECT ponto_de_entrada AS grupo, count(*) AS n FROM public.{tabela_dsp}
                   WHERE estado_do_ponto_de_entrada = ANY(%(uf_regional)s) AND ponto_de_entrada IS NOT NULL
                   GROUP BY grupo ORDER BY n DESC LIMIT 15""", uf_params)
                fig = go.Figure(go.Bar(x=df["n"], y=df["grupo"], orientation="h", marker_color=COR_CRITICO,
                    text=[f"{v:,}".replace(",",".") for v in df["n"]], textposition="outside", cliponaxis=False))
                fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#6B7A72"), height=460, margin=dict(l=10, r=65, t=5, b=5))
                fig.update_yaxes(autorange="reversed", automargin=True)
                st.plotly_chart(fig, use_container_width=True)
            with r2c2, st.container(border=True):
                st.markdown('<div class="chart-title">BACKLOG POR ENTREGADOR (TOP 15)</div>', unsafe_allow_html=True)
                df = run_query(f"""SELECT entregador AS grupo, count(*) AS n FROM public.{tabela_dsp}
                   WHERE estado_do_ponto_de_entrada = ANY(%(uf_regional)s) AND entregador IS NOT NULL
                   GROUP BY grupo ORDER BY n DESC LIMIT 15""", uf_params)
                fig = go.Figure(go.Bar(x=df["n"], y=df["grupo"], orientation="h", marker_color=ANJUN_GREEN,
                    text=[f"{v:,}".replace(",",".") for v in df["n"]], textposition="outside", cliponaxis=False))
                fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#6B7A72"), height=460, margin=dict(l=10, r=65, t=5, b=5))
                fig.update_yaxes(autorange="reversed", automargin=True)
                st.plotly_chart(fig, use_container_width=True)


# ===================== ABA COBRANÇA IATA =====================
with tab_cobranca:
    st.subheader("📋 Cobrança IATA — Espelho da aba DIN")
    st.caption(
        "Mesma lógica da aba DIN da planilha: todos os pedidos de cada DSP "
        "pela faixa de dias desde o inbound no ponto. A separação com/sem entregador "
        "é filtro adicional — não muda a base de cálculo."
    )

    opcoes_sup_cob = run_query(
        "SELECT DISTINCT supervisor FROM public.supervisores WHERE supervisor IS NOT NULL ORDER BY 1"
    )["supervisor"].tolist()

    col_f1, col_f2, col_f3, col_f4, col_f5 = st.columns([2, 2, 1, 1, 2])
    with col_f1:
        f_sup_cob = st.multiselect("Supervisor", opcoes_sup_cob, default=[], key="cob_supervisor")
    with col_f2:
        f_faixa_cob = st.multiselect(
            "Faixa (dias no DSP)",
            ["0 a 4 dias", "05 a 13 dias", "14 a 20 dias (Crítico)", "Mais de 20 (Extravio)"],
            default=[], key="cob_faixa",
            help="Mesmas faixas da aba DIN — dias desde o inbound no ponto.",
        )
    with col_f3:
        f_min_dias = st.number_input("Atraso mín. (dias)", min_value=0, max_value=365, value=0, key="cob_min_dias")
    with col_f4:
        apenas_anomalia = st.checkbox("Só anomalia", value=False, key="cob_anomalia")
    with col_f5:
        prazo_resposta = st.text_input("Prazo de resposta", value="18h de hoje", key="cob_prazo_resposta")

    sup_filtro = f_sup_cob if f_sup_cob else None
    df_din = _df_din(sup_filtro)

    df_din["Dias atraso"] = pd.to_numeric(df_din["Dias atraso"], errors="coerce")
    df_din["Dias no DSP"] = pd.to_numeric(df_din["Dias no DSP"], errors="coerce")
    df_din = df_din[~df_din["Motivo (raw)"].isin(MOTIVOS_EXCLUIR_COBRANCA)]

    if f_faixa_cob:
        df_din = df_din[df_din["Faixa"].isin(f_faixa_cob)]
    if f_min_dias > 0:
        df_din = df_din[df_din["Dias atraso"].fillna(0) >= f_min_dias]
    if apenas_anomalia:
        df_din = df_din[df_din["Status"].str.contains("anomalia", case=False, na=False)]

    if df_din.empty:
        st.warning("Nenhum pedido com os filtros aplicados.")
        st.stop()

    df_din["Urgência"]             = df_din["Dias atraso"].apply(lambda d: _urgencia_label(d)[0])
    df_din["Motivo da ocorrência"] = df_din["Motivo (raw)"].apply(_formatar_motivo)
    df_din["_tem_ent"]             = df_din["Entregador"].notna() & (df_din["Entregador"] != "")

    df_com_ent  = df_din[df_din["_tem_ent"]]
    df_sem_ent  = df_din[~df_din["_tem_ent"]]
    df_recebido = df_sem_ent[df_sem_ent["Status"] == "Recebido no ponto de entrega"]
    df_armaz    = df_sem_ent[df_sem_ent["Status"] == "Pacote armazenado"]
    df_rota_sr  = df_sem_ent[df_sem_ent["Status"] == "Em rota de entrega"]
    df_anom_sr  = df_sem_ent[df_sem_ent["Status"] == "Pedido com anomalia"]

    # Tabela pivô espelho da DIN
    st.markdown('<div class="section-title">📊 Resumo por DSP — Faixas de Tempo no Ponto (= aba DIN)</div>', unsafe_allow_html=True)
    ordem_faixas_din = ["0 a 4 dias", "05 a 13 dias", "14 a 20 dias (Crítico)", "Mais de 20 (Extravio)", "Sem data"]
    pivot_din = df_din.pivot_table(
        index="Ponto (IATA)", columns="Faixa", values="Waybill", aggfunc="count", fill_value=0
    ).reindex(columns=[c for c in ordem_faixas_din if c in df_din["Faixa"].unique()], fill_value=0)
    pivot_din["Total"]          = pivot_din.sum(axis=1)
    pivot_din["Supervisor"]     = df_din.groupby("Ponto (IATA)")["Supervisor"].first()
    pivot_din["Com entregador"] = df_din.groupby("Ponto (IATA)")["_tem_ent"].sum().astype(int)
    pivot_din["Sem entregador"] = pivot_din["Total"] - pivot_din["Com entregador"]
    pivot_din = pivot_din.sort_values("Total", ascending=False).reset_index()
    st.dataframe(pivot_din, use_container_width=True, hide_index=True)
    st.divider()

    # KPIs
    total_geral   = len(df_din)
    total_com_ent = len(df_com_ent)
    total_sem_ent = len(df_sem_ent)
    alertas       = df_din["Motivo (raw)"].isin(MOTIVOS_ALERTA.keys()).sum()
    critico_ext   = len(df_din[df_din["Faixa"].isin(["14 a 20 dias (Crítico)", "Mais de 20 (Extravio)"])])

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Total geral",           f"{total_geral:,}".replace(",", "."))
    k2.metric("👤 Com entregador",     f"{total_com_ent:,}".replace(",", "."))
    k3.metric("📦 Sem entregador",     f"{total_sem_ent:,}".replace(",", "."))
    k4.metric("🔴 Crítico + Extravio", f"{critico_ext:,}".replace(",", "."))
    k5.metric("🚨 Motivos críticos",   alertas,
              delta="ação imediata" if alertas > 0 else "nenhum",
              delta_color="inverse" if alertas > 0 else "normal")
    st.divider()

    # Sub-abas
    sub_com, sub_receb, sub_armaz_tab, sub_rota_sr, sub_anom_sr = st.tabs([
        f"👤 Com entregador ({total_com_ent:,})".replace(",", "."),
        f"📦 Recebido no hub ({len(df_recebido):,})".replace(",", "."),
        f"🗄️ Armazenado ({len(df_armaz):,})".replace(",", "."),
        f"🔄 Em rota s/ entregador ({len(df_rota_sr):,})".replace(",", "."),
        f"⚠️ Anomalia s/ entregador ({len(df_anom_sr):,})".replace(",", "."),
    ])

    def _render_ponto(df_sub, col_ponto, col_ent=None, key_pfx=""):
        if df_sub.empty:
            st.info("Nenhum pedido nessa situação com os filtros aplicados.")
            return
        for ponto in sorted(df_sub[col_ponto].dropna().unique()):
            df_p = df_sub[df_sub[col_ponto] == ponto].copy()
            sup_p    = df_p["Supervisor"].dropna().iloc[0] if not df_p["Supervisor"].dropna().empty else "—"
            total_p  = len(df_p)
            crit_p   = len(df_p[df_p["Dias atraso"].fillna(0) >= 14])
            ant_p    = int(df_p["Dias no DSP"].max()) if not df_p["Dias no DSP"].isna().all() else "—"
            emoji_p  = "🔴" if crit_p == total_p else "🟠" if crit_p > total_p * 0.5 else "🟡"
            with st.expander(
                f"{emoji_p} **{ponto}** — {total_p} pedido(s) · {crit_p} crítico(s) · max {ant_p}d no DSP · Sup: {sup_p}",
                expanded=(total_p >= 3),
            ):
                al_p = df_p["Motivo (raw)"].isin(MOTIVOS_ALERTA.keys()).sum()
                if al_p > 0:
                    resumo_al = ", ".join(
                        f"{m} ({n}x)" for m, n in
                        df_p[df_p["Motivo (raw)"].isin(MOTIVOS_ALERTA.keys())]["Motivo da ocorrência"].value_counts().items()
                    )
                    st.warning(f"🚨 **{al_p} pedido(s) com motivo crítico:** {resumo_al}")
                cols_base = ["Waybill", "Urgência", "Dias no DSP", "Dias atraso",
                             "Motivo da ocorrência", "Prazo", "Cidade", "UF", "Status"]
                if col_ent:
                    cols_base = [col_ent] + cols_base
                cols_show = [c for c in cols_base if c in df_p.columns]
                df_show = df_p[cols_show].sort_values("Dias no DSP", ascending=False, na_position="last")
                st.dataframe(df_show, use_container_width=True, hide_index=True,
                    column_config={
                        "Waybill":              st.column_config.TextColumn("Waybill", width="medium"),
                        "Urgência":             st.column_config.TextColumn("Urgência", width="medium"),
                        "Dias no DSP":          st.column_config.NumberColumn("Dias no DSP", format="%d",
                                                    help="Dias desde o inbound no ponto — mesma métrica da aba DIN"),
                        "Dias atraso":          st.column_config.NumberColumn("Dias atraso (prazo)", format="%d"),
                        "Motivo da ocorrência": st.column_config.TextColumn("Motivo", width="large"),
                        "Prazo":                st.column_config.DatetimeColumn("Prazo", format="DD/MM/YYYY"),
                    })
                st.download_button(
                    f"⬇️ CSV — {ponto}",
                    df_show.to_csv(index=False).encode("utf-8"),
                    f"{key_pfx}_{ponto}_{datetime.now():%Y%m%d}.csv",
                    mime="text/csv", key=f"dl_{key_pfx}_{ponto}",
                )

    with sub_com:
        st.caption("Pedidos que saíram para rota com entregador nominado.")
        _render_ponto(df_com_ent, "Ponto (IATA)", col_ent="Entregador", key_pfx="com_ent")
        st.divider()
        col_e1, col_e2 = st.columns(2)
        with col_e1:
            st.download_button("⬇️ CSV completo (com entregador)",
                df_com_ent.to_csv(index=False).encode("utf-8"),
                f"cobranca_com_entregador_{datetime.now():%Y%m%d}.csv",
                mime="text/csv", key="dl_com_completo")
        with col_e2:
            linhas_msg = []
            for ponto, grp_p in df_com_ent.groupby("Ponto (IATA)"):
                sup_msg = grp_p["Supervisor"].dropna().iloc[0] if not grp_p["Supervisor"].dropna().empty else "—"
                linhas_msg.append(f"\n📍 *{ponto}* — {len(grp_p)} pedido(s) | Sup: {sup_msg}")
                for ent, grp_e in grp_p.groupby("Entregador"):
                    dsp_max = grp_e["Dias no DSP"].max()
                    dsp_str = f"{int(dsp_max)}d no DSP" if not pd.isna(dsp_max) else "s/ data"
                    linhas_msg.append(f"\n  👤 *{ent}* — {len(grp_e)} pedido(s) · max {dsp_str}")
                    al_e = grp_e[grp_e["Motivo (raw)"].isin(MOTIVOS_ALERTA.keys())]
                    norm_e = grp_e[~grp_e["Motivo (raw)"].isin(MOTIVOS_ALERTA.keys())]
                    if not al_e.empty:
                        linhas_msg.append("    ⚠️ *Casos críticos:*")
                        for _, row in al_e.sort_values("Dias no DSP", ascending=False, na_position="last").iterrows():
                            d = f"{int(row['Dias no DSP'])}d" if not pd.isna(row["Dias no DSP"]) else "—"
                            linhas_msg.append(f"    🚨 {row['Waybill']} · {d} · {MOTIVOS_ALERTA.get(str(row['Motivo (raw)']).strip(),'')}")
                    if not norm_e.empty:
                        if not al_e.empty: linhas_msg.append("    ─────────")
                        for _, row in norm_e.sort_values("Dias no DSP", ascending=False, na_position="last").iterrows():
                            d = f"{int(row['Dias no DSP'])}d" if not pd.isna(row["Dias no DSP"]) else "—"
                            linhas_msg.append(f"    • {row['Waybill']} · {d}")
            prazo_msg = prazo_resposta.strip() or "18h de hoje"
            linhas_msg.append(f"\n{'─'*35}")
            linhas_msg.append(f"⏰ Aguardo posicionamento até *{prazo_msg}*.")
            linhas_msg.append("Confirme o recebimento e informe o status de cada pedido.")
            st.download_button("📱 Lista WhatsApp",
                "\n".join(linhas_msg).encode("utf-8"),
                f"whatsapp_com_entregador_{datetime.now():%Y%m%d}.txt",
                mime="text/plain", key="dl_com_whats")
            st.caption("Críticos primeiro → normais → rodapé com prazo.")

    with sub_receb:
        st.caption("Chegaram no DSP mas não saíram para nenhum entregador. Cobrar o gestor do DSP.")
        _render_ponto(df_recebido, "Ponto (IATA)", key_pfx="recebido")
        if not df_recebido.empty:
            st.divider()
            st.download_button("⬇️ CSV — Recebidos no hub",
                df_recebido.to_csv(index=False).encode("utf-8"),
                f"cobranca_recebido_{datetime.now():%Y%m%d}.csv",
                mime="text/csv", key="dl_recebido")

    with sub_armaz_tab:
        st.caption("Status: Pacote armazenado — parado em armazenamento temporário sem previsão de saída.")
        _render_ponto(df_armaz, "Ponto (IATA)", key_pfx="armaz")
        if not df_armaz.empty:
            st.divider()
            st.download_button("⬇️ CSV — Armazenados",
                df_armaz.to_csv(index=False).encode("utf-8"),
                f"cobranca_armazenado_{datetime.now():%Y%m%d}.csv",
                mime="text/csv", key="dl_armaz")

    with sub_rota_sr:
        st.caption("Status: Em rota, mas sem entregador registrado. Investigar quem fez a rota no DSP.")
        _render_ponto(df_rota_sr, "Ponto (IATA)", key_pfx="rota_sr")
        if not df_rota_sr.empty:
            st.divider()
            st.download_button("⬇️ CSV — Rota s/ entregador",
                df_rota_sr.to_csv(index=False).encode("utf-8"),
                f"cobranca_rota_sr_{datetime.now():%Y%m%d}.csv",
                mime="text/csv", key="dl_rota_sr")

    with sub_anom_sr:
        st.caption("Anomalia sem entregador nominado — falha sem responsável claro no sistema.")
        _render_ponto(df_anom_sr, "Ponto (IATA)", key_pfx="anom_sr")
        if not df_anom_sr.empty:
            st.divider()
            st.download_button("⬇️ CSV — Anomalia s/ entregador",
                df_anom_sr.to_csv(index=False).encode("utf-8"),
                f"cobranca_anomalia_sr_{datetime.now():%Y%m%d}.csv",
                mime="text/csv", key="dl_anom_sr")