"""Web page for the setlist builder. Run locally with: streamlit run app.py"""
import io
import datetime

import streamlit as st

import setlist_engine

st.set_page_config(page_title="Setlist Builder", page_icon="🎶", layout="wide")
st.title("🎶 Setlist Builder")
st.caption("Pick who's out and how long the gig is. The setlist is built from the group's Google Sheet.")


@st.cache_data(ttl=60)
def load():
    return setlist_engine.load_sheet()


try:
    df_songs, df_members, df_banned = load()
except Exception as err:
    st.error(f"Couldn't read the Google Sheet. Check that it's still shared publicly.\n\n{err}")
    st.stop()

members = sorted(df_members["Names"].dropna().tolist())
all_songs = df_songs["Song"].tolist()
holiday_songs = df_songs[df_songs["Type"].isin(["holiday", "current parody"])]["Song"].tolist()

with st.sidebar:
    st.header("Gig details")
    gig_name = st.text_input("Gig name", "Sample gig")
    gig_date = st.date_input("Date", datetime.date.today())
    num_songs = st.number_input("Number of songs", min_value=1, max_value=len(all_songs), value=min(12, len(all_songs)))
    num_breaks = st.selectbox("Number of breaks", [0, 1, 2, 3])
    holiday = st.checkbox("Holiday gig", help="Uses only holiday and current parody songs.")
    if st.button("Reload sheet", help="Use this if you just edited the Google Sheet."):
        load.clear()
        st.rerun()

col1, col2 = st.columns(2)
with col1:
    absent = st.multiselect("Who's absent?", members)
with col2:
    removable = [s for s in all_songs if not (holiday and s not in holiday_songs)]
    removed = st.multiselect("Songs to leave out", removable)

if holiday:
    removed = removed + [s for s in all_songs if s not in holiday_songs]

if st.button("Build setlist", type="primary"):
    try:
        result = setlist_engine.build_setlist(
            df_songs, df_members, df_banned, absent, removed,
            int(num_songs), int(num_breaks), holiday,
        )
    except setlist_engine.SetlistError as err:
        st.error(f"There's a problem in the spreadsheet: {err}")
        st.stop()

    if result["success"]:
        st.success("Setlist ready!")
    else:
        st.warning("Couldn't find an order that works. Here are all the songs you can do.")

    st.dataframe(result["ordered"], hide_index=True, width="stretch")

    if result["notes"]:
        with st.expander("Notes (substitutions and warnings)", expanded=True):
            for note in result["notes"]:
                st.write("• " + note)

    if result["unavailable_songs"]:
        with st.expander(f"Can't do these songs ({len(result['unavailable_songs'])})"):
            st.dataframe(result["unavailable"], hide_index=True, width="stretch")

    buffer = io.BytesIO()
    with setlist_engine.pandas.ExcelWriter(buffer, engine="xlsxwriter") as writer:
        result["ordered"].to_excel(writer, sheet_name="Setlist", index=False)
        result["unavailable"].to_excel(writer, sheet_name="Unavailable", index=False)
    st.download_button(
        "Download Excel",
        buffer.getvalue(),
        file_name=f"{gig_name} {gig_date}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
