"""Healthcare analytics platform — entry point.  Run: streamlit run app.py"""

import streamlit as st

from profit_pools.design.theme import css, register_plotly_template
from profit_pools.modules.registry import modules
from profit_pools.shell import data, layout

st.set_page_config(page_title="Profit Pools Explorer", page_icon=":material/stacked_bar_chart:",
                   layout="wide", initial_sidebar_state="expanded")
register_plotly_template()
st.markdown(css(), unsafe_allow_html=True)

pages = [st.Page(m.render, title=m.title, icon=m.icon, url_path=m.url_path, default=i == 0)
         for i, m in enumerate(modules())]
current = st.navigation(pages, position="hidden")
layout.sidebar(pages, data.adapter())
current.run()
