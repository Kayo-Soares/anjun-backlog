# Backlog — Anjun Brasil

App Streamlit para subir a base de backlog e acompanhar pacotes parados
(dias sem movimentação, dias desde o recebimento no ponto). Mesmo banco
Supabase dos outros apps (Pontualidade, Central de Indicadores).

## Estrutura
```
backlog_streamlit/
├── app.py
├── requirements.txt
├── assets/anjun_logo.png
├── config/column_mapping_base.json   # mesmo mapeamento do app de Pontualidade
└── .streamlit/
    ├── config.toml
    └── secrets.toml.example
```

## Como rodar
1. `pip install -r requirements.txt`
2. `cp .streamlit/secrets.toml.example .streamlit/secrets.toml` e preenche a senha
   (mesma senha do banco dos outros apps)
3. `streamlit run app.py`

## Diferença importante em relação ao app de Pontualidade

O casamento de colunas aqui é por **texto do cabeçalho**, não por posição
fixa — porque o arquivo de backlog pode vir com colunas extras no meio
(o time já usa "Dia de recebimento"/"Dias compilados" como cálculo manual
no Excel). Essas colunas extras são detectadas e ignoradas automaticamente;
os indicadores equivalentes são recalculados pela view `backlog_atual` no
banco, sempre em relação à data de hoje (nunca ficam desatualizados).

## Deploy
Mesmo processo dos outros apps: GitHub + Streamlit Community Cloud,
colando o `secrets.toml` em Settings → Secrets.
