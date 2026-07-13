# LAST UPDATED: 7-13-2026. Added margins to the tool doesn't stretch the entire page width

# --- ENVIRONMENT SETUP REFERENCE (KEEP FOR REFERENCE) ---

### 1. Create a virtual environment named 'atlas_env' 
# FIX: Changed 'cpython' to the correct standard 'python3' command
# python3 -m venv atlas_env

### 2. Activate it (Mac)
# source atlas_env/bin/activate

### 3. Activate it (Windows)
# .\atlas_env\Scripts\activate

### 4. Install the required core web-mapping packages
# pip install panel geemap geopandas nbconvert jupyter_bokeh pyogrio folium

### 5. Launch the local interactive server tab straight to the browser
# panel serve app.py --show

#CTRL + C in the terminal to stop the server when done

# --- END REFERENCE ---
import os
import json
import warnings
import math
import geopandas as gpd
import pandas as pd
import panel as pn
import plotly.graph_objects as go

# --- Initialize Panel & Apply Explicit 150px Symmetrical Outer Margins ---
pn.extension('plotly', design='bootstrap')

pn.config.raw_css.append("""
/* Fast Design's body has NO background-color set by default (just margin:0
   and overflow:hidden) — confirmed by reading Panel's own shipped fast.css.
   Setting it explicitly so any margin/gap anywhere on the page shows this
   dark tone instead of the browser's raw default. */
body {
    background-color: #181818 !important;
}

/* 1. LEFT SIDEBAR: Push it 150px away from the left screen edge */
#sidebar {
    margin-left: 150px !important;
}

/* 2. RIGHT SIDEBAR: #right-sidebar has no explicit CSS position set in Fast
   Design's own stylesheet (confirmed by reading it directly) — it's a normal
   flex child, same as #sidebar, defaulting to position:static. That means
   `right:` has ZERO effect on it (that property only works on positioned
   elements). Using margin-right instead, exactly mirroring how #sidebar
   already correctly uses margin-left. */
#right-sidebar {
    margin-right: 150px !important;
}

/* 3. CENTER CONTENT: Adjust the main area so it doesn't get squished or offset improperly */
#main {
    box-sizing: border-box;
}

/* Responsive fallback: Collapse the massive margins on smaller viewports so the map stays functional */
@media (max-width: 1450px) {
    #sidebar {
        margin-left: 0px !important;
    }
    #right-sidebar {
        margin-right: 0px !important;
    }
}
""")

base_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in locals() else os.getcwd()

# --- JPS Blended Residential Rate (late 2025 / early 2026) ---
# Reference only — av_CONS is already in JMD, so this is NOT applied to it directly.
# NOTE: previously had a JPS_BLENDED_RATE_JMD_PER_KWH constant here ($46/kWh) as a
# stand-in for real billing data. Removed once JSLC 2023 gave us actual reported
# electricity bills (electric field) — the estimate had no traceable citation and
# is no longer needed now that real survey data exists.

# --- 1. Load Local Data ---
# NOTE: filenames were originally swapped at the source (grab_data.py scrape) —
# jamaica_consumption_2012.geojson now correctly holds av_CONS (consumption),
# jamaica_poverty_2012.geojson now correctly holds Per_Tot_Po (poverty rate).
parish_gdf      = gpd.read_file(os.path.join(base_dir, "assets", "ja_parishes.json"))
poverty_gdf     = gpd.read_file(os.path.join(base_dir, "assets", "jamaica_poverty_2012.geojson"))
consumption_gdf = gpd.read_file(os.path.join(base_dir, "assets", "jamaica_consumption_2012.geojson"))
grid_gdf        = gpd.read_file(os.path.join(base_dir, "assets", "overpass_energy_infra_2.geojson"))
dwel_df         = pd.read_csv(os.path.join(base_dir, "assets", "PopulationandDwellingCountsbyCommunity_.csv"))
burden_df       = pd.read_csv(os.path.join(base_dir, "assets", "jslc_2023_parish_burden.csv"))

# Normalize burden_df's parish names to match the same key system as everything else
burden_df['_parish_key'] = burden_df['parish_name'].apply(
    lambda x: " ".join(str(x).upper().replace(".", "").replace("SAINT", "ST").split())
)

# --- CRS FIX ---
# poverty_gdf / consumption_gdf report .crs as EPSG:4326 but coordinate VALUES are
# actually in Web Mercator meters. Force-assign the real CRS, then reproject properly.
def fix_crs(gdf):
    gdf = gdf.set_crs("EPSG:3857", allow_override=True)
    return gdf.to_crs("EPSG:4326")

poverty_gdf     = fix_crs(poverty_gdf)
consumption_gdf = fix_crs(consumption_gdf)

# Keep numeric copies of key fields before the stringify pass below
poverty_gdf['Per_Tot_Po_numeric']  = pd.to_numeric(poverty_gdf['Per_Tot_Po'], errors='coerce')
consumption_gdf['av_CONS_numeric'] = pd.to_numeric(consumption_gdf['av_CONS'], errors='coerce')

for df in [parish_gdf, poverty_gdf, consumption_gdf, grid_gdf]:
    for col in df.columns:
        if col != 'geometry' and not col.endswith('_numeric'):
            df[col] = df[col].astype(str)

# Filter to valid geometry types only
valid_types = ['Point', 'MultiPoint', 'LineString', 'MultiLineString', 'Polygon', 'MultiPolygon']
grid_gdf = grid_gdf[grid_gdf.geometry.geom_type.isin(valid_types)].copy()

transmission_gdf = grid_gdf[grid_gdf['power'] == 'line'].copy()

plants_gdf = grid_gdf[grid_gdf['power'] == 'plant'].copy()
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    plants_gdf['geometry'] = plants_gdf.geometry.centroid

# --- Parish name normalization ---
def normalize_parish(name):
    if not isinstance(name, str):
        return ""
    cleaned = name.upper().replace(".", "")
    cleaned = cleaned.replace("SAINT", "ST")
    return " ".join(cleaned.split())

parish_gdf['_parish_key']      = parish_gdf['name'].apply(normalize_parish)
poverty_gdf['_parish_key']     = poverty_gdf['PARISH'].apply(normalize_parish)
consumption_gdf['_parish_key'] = consumption_gdf['PARISH'].apply(normalize_parish)
if 'Parish' in dwel_df.columns:
    dwel_df['_parish_key'] = dwel_df['Parish'].apply(normalize_parish)

parish_list = sorted(parish_gdf['name'].dropna().unique().tolist())
parish_name_to_key = dict(zip(parish_gdf['name'], parish_gdf['_parish_key']))

# Join burden values onto parish_gdf once, for the choropleth layer.
parish_gdf = parish_gdf.merge(
    burden_df[['_parish_key', 'avg_burden_pct']],
    on='_parish_key',
    how='left'
)

def get_community_list(selected_parish):
    if selected_parish == 'Island-Wide':
        return ['All Communities']
    key = parish_name_to_key.get(selected_parish)
    df = poverty_gdf[poverty_gdf['_parish_key'] == key]
    if 'COMM_NAME' in df.columns:
        return ['All Communities'] + sorted(df['COMM_NAME'].dropna().unique().tolist())
    return ['All Communities']

# --- 2. Widgets ---
parish_selector = pn.widgets.Select(
    name='Select Parish',
    options=['Island-Wide'] + parish_list,
    width=270
)

community_selector = pn.widgets.Select(
    name='Select Community',
    options=['All Communities'],
    width=270
)

def update_communities(event):
    community_selector.options = get_community_list(event.new)
    community_selector.value = 'All Communities'

parish_selector.param.watch(update_communities, 'value')

# --- 3. Plotly Map Builder ---
PARISH_BOUNDARY_COLOR = '#FFFFFF'

def compute_bounds_zoom(minx, miny, maxx, maxy):
    """Rough zoom heuristic based on bounding box span in degrees."""
    span = max(maxx - minx, maxy - miny)
    if span <= 0:
        return 10
    zoom = math.log2(360 / span) - 1
    return max(8, min(zoom, 13))

def build_figure(selected_parish, selected_community):
    fig = go.Figure()

    # --- Parish Boundaries — plain outline, always shown ---
    fig.add_trace(go.Choroplethmapbox(
        geojson=parish_geojson_data,
        locations=parish_gdf['name'],
        z=[0] * len(parish_gdf),
        featureidkey="properties.name",
        colorscale=[[0, 'rgba(0,0,0,0)'], [1, 'rgba(0,0,0,0)']],
        marker_line_width=1.5,
        marker_line_color=PARISH_BOUNDARY_COLOR,
        showscale=False,
        hovertemplate="<b>Parish:</b> %{location}<extra></extra>",
        name="Parish Boundaries",
        showlegend=True,
    ))

    # --- Energy Burden Choropleth — Island-Wide only ---
    if selected_parish == 'Island-Wide':
        fig.add_trace(go.Choroplethmapbox(
            geojson=parish_geojson_data,
            locations=parish_gdf['name'],
            z=parish_gdf['avg_burden_pct'],
            featureidkey="properties.name",
            colorscale=[[0, '#1a3a4a'], [0.5, '#F5A623'], [1, '#FF3D00']],
            marker_line_width=1,
            marker_line_color='#0f2535',
            marker_opacity=0.75,
            showscale=False,
            hovertemplate="<b>Parish:</b> %{location}<br><b>Avg Energy Burden:</b> %{z:.1f}%<extra></extra>",
            name="Energy Burden (%, JSLC 2023)",
            showlegend=True,
        ))

    # --- Communities Choropleth — only when a parish is selected ---
    if selected_parish != 'Island-Wide':
        key = parish_name_to_key.get(selected_parish)
        communities_to_show = poverty_gdf[poverty_gdf['_parish_key'] == key].copy()

        if selected_community != 'All Communities' and 'COMM_NAME' in communities_to_show.columns:
            communities_to_show = communities_to_show[
                communities_to_show['COMM_NAME'] == selected_community
            ]

        communities_to_show = communities_to_show.reset_index(drop=True)
        communities_to_show['_comm_display'] = [
            row['COMM_NAME'] if isinstance(row['COMM_NAME'], str) and row['COMM_NAME'].strip() not in ('', 'nan', 'None')
            else f'(Unnamed community #{i+1})'
            for i, row in communities_to_show.iterrows()
        ]
        communities_to_show['_comm_id'] = communities_to_show.index.astype(str)

        if len(communities_to_show) > 0:
            comm_geojson = json.loads(communities_to_show.to_json())
            for i, feat in enumerate(comm_geojson['features']):
                feat['id'] = str(i)

            custom = communities_to_show[['_comm_display', 'COMM_NAME', 'PARISH']].values

            fig.add_trace(go.Choroplethmapbox(
                geojson=comm_geojson,
                locations=communities_to_show['_comm_id'],
                z=communities_to_show['Per_Tot_Po_numeric'],
                featureidkey="id",
                colorscale=[[0, '#1a3a4a'], [0.5, '#F5A623'], [1, '#FF3D00']],
                marker_line_width=1,
                marker_line_color='#0f2535',
                marker_opacity=0.8,
                showscale=False,
                customdata=custom,
                hovertemplate="<b>Community:</b> %{customdata[0]}<br><b>Poverty Rate:</b> %{z:.1f}%<extra></extra>",
                name="Communities",
                showlegend=True,
            ))

    # --- Transmission Lines ---
    lats, lons = [], []
    for _, row in transmission_gdf.iterrows():
        geom = row.geometry
        geoms = geom.geoms if geom.geom_type == 'MultiLineString' else [geom]
        for line in geoms:
            xs, ys = line.xy
            lons.extend(list(xs))
            lats.extend(list(ys))
            lons.append(None)
            lats.append(None)
    fig.add_trace(go.Scattermapbox(
        lat=lats, lon=lons, mode='lines',
        line=dict(width=2, color='#F5A623'),
        hoverinfo='skip',
        name="Transmission Lines",
    ))

    # --- Power Plants ---
    plant_rows = plants_gdf[plants_gdf['name'].apply(
        lambda x: isinstance(x, str) and x.strip().lower() not in ('', 'nan', 'none')
    )]
    if len(plant_rows) > 0:
        fig.add_trace(go.Scattermapbox(
            lat=plant_rows.geometry.y.tolist(),
            lon=plant_rows.geometry.x.tolist(),
            mode='markers',
            marker=dict(size=14, color='#FF3D00'),
            text=[f"{n}<br>Fuel: {s}<br>Capacity: {c}" for n, s, c in zip(
                plant_rows['name'], plant_rows.get('plant:source', ['Unknown']*len(plant_rows)),
                plant_rows.get('plant:output:electricity', ['Unknown']*len(plant_rows))
            )],
            hovertemplate="%{text}<extra></extra>",
            name="Power Plants",
        ))

    # --- View / zoom ---
    if selected_parish != 'Island-Wide':
        match = parish_gdf[parish_gdf['name'] == selected_parish]
        if len(match) > 0:
            minx, miny, maxx, maxy = match.total_bounds
            center = {'lat': (miny + maxy) / 2, 'lon': (minx + maxx) / 2}
            zoom = compute_bounds_zoom(minx, miny, maxx, maxy)
        else:
            center = {'lat': 18.15, 'lon': -77.3}
            zoom = 9
    else:
        center = {'lat': 18.15, 'lon': -77.3}
        zoom = 8

    fig.update_layout(
        mapbox_style='carto-darkmatter',
        mapbox_center=center,
        mapbox_zoom=zoom,
        margin=dict(l=0, r=0, t=0, b=0),
        showlegend=True,
        legend=dict(
            bgcolor='#0f2535',
            bordercolor='#2a4a5a',
            borderwidth=1,
            font=dict(color='#8fa8b8', size=11, family='monospace'),
            x=0.01, y=0.99, xanchor='left', yanchor='top',
            title=dict(text='Layers (click to toggle)', font=dict(color='#F5A623', size=10)),
        ),
        paper_bgcolor='#0f2535',
        plot_bgcolor='#0f2535',
        height=500,
        hoverlabel=dict(bgcolor='#0f2535', font_color='#F5A623', font_family='monospace'),
    )
    return fig

parish_geojson_data = json.loads(parish_gdf.to_json())

plotly_pane = pn.pane.Plotly(
    build_figure('Island-Wide', 'All Communities'),
    height=500,
    sizing_mode='stretch_both',
    config={
        'displayModeBar': True,
        'scrollZoom': True,
        'displaylogo': False,
        'modeBarButtonsToRemove': [
            'select2d', 'lasso2d', 'autoScale2d', 'toImage'
        ],
    },
)

@pn.depends(parish_selector.param.value, community_selector.param.value, watch=True)
def _update_map_figure(selected_parish, selected_community):
    plotly_pane.object = build_figure(selected_parish, selected_community)

def _handle_map_click(event):
    click_data = event.new
    if not click_data:
        return
    points = click_data.get('points', [])
    if not points:
        return
    point = points[0]
    customdata = point.get('customdata')
    if customdata and len(customdata) > 1:
        comm_name = customdata[1]  # COMM_NAME
        if isinstance(comm_name, str) and comm_name.strip() not in ('', 'nan', 'None'):
            community_selector.value = comm_name

plotly_pane.param.watch(_handle_map_click, 'click_data')

def map_view():
    return plotly_pane

# --- 5. Reactive Parish Info ---
@pn.depends(parish_selector.param.value, community_selector.param.value)
def parish_info(selected_parish, selected_community):
    if selected_parish == 'Island-Wide':
        text = "Viewing all 14 parishes. Select a parish to see its communities on the map."
    else:
        key = parish_name_to_key.get(selected_parish)
        subset = poverty_gdf[poverty_gdf['_parish_key'] == key]
        n = len(subset)
        if selected_community != 'All Communities':
            text = f"**{selected_parish} → {selected_community}**\n\nZoomed to selected community."
        else:
            text = f"**{selected_parish}**\n\n{n} communities now visible on map. Hover to see details."
    return pn.pane.Markdown(text, styles={"color": "#8fa8b8", "font-size": "13px"})

# --- 6. Metric Cards ---
def metric_card(label, value, color="#F5A623"):
    return pn.pane.HTML(f"""
        <div style="background:#0f2535; border-left:4px solid {color}; border-radius:6px;
                    padding:12px 16px; margin-bottom:10px; font-family:monospace;">
            <div style="color:#8fa8b8; font-size:11px; text-transform:uppercase; letter-spacing:1px;">{label}</div>
            <div style="color:{color}; font-size:24px; font-weight:700; margin-top:4px;">{value}</div>
        </div>""", width=280)

# --- 7. Reactive Bottom Metrics Bar ---
def lookup_dwelling_row(parish_display_name, community_name):
    if 'Community' not in dwel_df.columns:
        return None
    key = parish_name_to_key.get(parish_display_name)
    subset = dwel_df[dwel_df['_parish_key'] == key] if key and '_parish_key' in dwel_df.columns else dwel_df
    match = subset[subset['Community'].astype(str).str.strip().str.upper() == community_name.strip().upper()]
    return match.iloc[0] if len(match) > 0 else None

def lookup_poverty_row(parish_display_name, community_name):
    key = parish_name_to_key.get(parish_display_name)
    subset = poverty_gdf[poverty_gdf['_parish_key'] == key] if key else poverty_gdf
    if 'COMM_NAME' not in subset.columns:
        return None
    match = subset[subset['COMM_NAME'].astype(str).str.strip().str.upper() == community_name.strip().upper()]
    return match.iloc[0] if len(match) > 0 else None

def lookup_consumption_row(parish_display_name, community_name):
    key = parish_name_to_key.get(parish_display_name)
    subset = consumption_gdf[consumption_gdf['_parish_key'] == key] if key else consumption_gdf
    if 'COMM_NAME' not in subset.columns:
        return None
    match = subset[subset['COMM_NAME'].astype(str).str.strip().str.upper() == community_name.strip().upper()]
    return match.iloc[0] if len(match) > 0 else None

def lookup_burden_row(parish_display_name):
    """JSLC 2023 parish-level energy burden — real survey data, not modeled."""
    key = parish_name_to_key.get(parish_display_name)
    if key is None:
        return None
    match = burden_df[burden_df['_parish_key'] == key]
    return match.iloc[0] if len(match) > 0 else None

ISLAND_AVG_BURDEN = burden_df['avg_burden_pct'].mean()

def stat_block(label, value, sublabel="", color="#fff"):
    sub_html = f'<div style="color:#8fa8b8; font-size:9px;">{sublabel}</div>' if sublabel else ""
    return f"""
        <div>
            <div style="color:#8fa8b8; font-size:10px; margin-bottom:4px;">{label}</div>
            <div style="color:{color}; font-size:28px; font-weight:700;">{value}</div>
            {sub_html}
        </div>"""

@pn.depends(parish_selector.param.value, community_selector.param.value)
def bottom_bar(selected_parish, selected_community):
    if selected_parish == 'Island-Wide':
        html = f"""
        <div style="background:#0f2535; padding:32px 32px; border-top:2px solid #F5A623;
                    font-family:monospace; width:100%; box-sizing:border-box;">
            <div style="color:#F5A623; font-size:11px; text-transform:uppercase;
                        letter-spacing:2px; margin-bottom:20px;">Island-Wide Snapshot</div>
            <div style="display:flex; gap:40px; flex-wrap:wrap; margin-bottom:28px;">
                {stat_block("Total Parishes", "14")}
                {stat_block("Population (2021)", "2.73M")}
                {stat_block("Grid Connected", "~96%")}
                {stat_block("Renewables Share", "~15%")}
                {stat_block("Vision 2030 Target", "50%", color="#F5A623")}
                {stat_block("Avg Energy Burden", f"{ISLAND_AVG_BURDEN:.1f}%", sublabel="JSLC 2023 survey, 14-parish avg", color="#FF8C00")}
            </div>
            <div style="color:#8fa8b8; font-size:11px;">
                Select a specific Parish above to see that parish's energy burden, or drill into a Community for local data.
            </div>
        </div>"""
        return pn.pane.HTML(html, sizing_mode='stretch_width', margin=0)

    if selected_community == 'All Communities':
        burden_row = lookup_burden_row(selected_parish)
        if burden_row is not None:
            burden_display = f"{burden_row['avg_burden_pct']:.1f}%"
            n_hh = int(burden_row['n_households'])
            elec_avg = burden_row['avg_electric_bill']
            exp_avg = burden_row['avg_tot_exp']
        else:
            burden_display = "N/A"
            n_hh = 0
            elec_avg = None
            exp_avg = None

        html = f"""
        <div style="background:#0f2535; padding:32px 32px; border-top:2px solid #F5A623;
                    font-family:monospace; width:100%; box-sizing:border-box;">
            <div style="color:#F5A623; font-size:11px; text-transform:uppercase;
                        letter-spacing:2px; margin-bottom:20px;">{selected_parish} Snapshot</div>
            <div style="display:flex; gap:40px; flex-wrap:wrap; margin-bottom:28px;">
                {stat_block("Avg Energy Burden", burden_display, sublabel=f"JSLC 2023, n={n_hh} households", color="#FF8C00")}
                {stat_block("Avg Electricity Bill", f"${elec_avg:,.0f} JMD/yr" if elec_avg is not None else "N/A", sublabel="JSLC 2023 survey")}
                {stat_block("Avg Total Expenditure", f"${exp_avg:,.0f} JMD/yr" if exp_avg is not None else "N/A", sublabel="JSLC 2023 survey")}
                {stat_block("Households Surveyed", f"{n_hh}", sublabel="JSLC 2023 sample size, this parish")}
            </div>
            <div style="color:#8fa8b8; font-size:11px;">
                Select a Community above to see community-level consumption and poverty data (2012 small-area model).
            </div>
        </div>"""
        return pn.pane.HTML(html, sizing_mode='stretch_width', margin=0)

    dwel_row        = lookup_dwelling_row(selected_parish, selected_community)
    poverty_row     = lookup_poverty_row(selected_parish, selected_community)
    consumption_row = lookup_consumption_row(selected_parish, selected_community)

    pop_val  = dwel_row['Total Population']    if dwel_row is not None and 'Total Population' in dwel_row else "N/A"
    dwel_val = dwel_row['Number of Dwellings'] if dwel_row is not None and 'Number of Dwellings' in dwel_row else "N/A"
    pov_val  = poverty_row['Per_Tot_Po']       if poverty_row is not None and 'Per_Tot_Po' in poverty_row else None

    cons_numeric = None
    if consumption_row is not None and 'av_CONS_numeric' in consumption_row:
        val = consumption_row['av_CONS_numeric']
        if pd.notna(val):
            cons_numeric = val

    if cons_numeric is not None:
        cons_display = f"${cons_numeric:,.0f} JMD"
        cons_sublabel = "Est. annual household consumption (2012 small-area model)"
    else:
        cons_display = "No data"
        cons_sublabel = "Not available for this community"

    try:
        pov_display = f"{float(pov_val):.1f}%"
    except (TypeError, ValueError):
        pov_display = "N/A"

    missing_note = ""
    if dwel_row is None or poverty_row is None or cons_numeric is None:
        missing_note = """<div style="color:#FF8C00; font-size:10px; margin-top:16px;">
            ⚠ Some data not found for this community — coverage varies by source dataset.
            </div>"""

    html = f"""
    <div style="background:#0f2535; padding:32px 32px; border-top:2px solid #F5A623;
                font-family:monospace; width:100%; box-sizing:border-box;">
        <div style="color:#F5A623; font-size:11px; text-transform:uppercase;
                    letter-spacing:2px; margin-bottom:20px;">{selected_community}, {selected_parish}</div>
        <div style="display:flex; gap:40px; flex-wrap:wrap;">
            {stat_block("Total Population", pop_val)}
            {stat_block("Number of Dwellings", dwel_val)}
            {stat_block("Avg Household Consumption", cons_display, sublabel=cons_sublabel)}
            {stat_block("Poverty Rate", pov_display, color="#FF8C00")}
        </div>
        {missing_note}
    </div>"""
    return pn.pane.HTML(html, sizing_mode='stretch_width', margin=0)

# --- 8. Legend ---
def static_legend_top():
    return """
<div style="background:#0f2535; padding:15px; border-radius:6px; font-family:monospace; color:#8fa8b8;">
    <div style="color:#F5A623; font-size:12px; text-transform:uppercase; letter-spacing:1px; margin-bottom:12px;">Legend</div>
    <div style="display:flex; align-items:center; margin-bottom:8px;">
        <div style="width:14px; height:14px; background:#FF3D00; border-radius:50%; margin-right:10px;"></div> Power Plants
    </div>
    <div style="display:flex; align-items:center; margin-bottom:8px;">
        <div style="width:20px; height:3px; background:#F5A623; margin-right:10px;"></div> Transmission Lines
    </div>
    <div style="display:flex; align-items:center; margin-bottom:8px;">
        <div style="width:20px; height:3px; background:#A0A0A0; margin-right:10px;"></div> Parish Boundaries
    </div>
    <div style="font-size:10px; color:#8fa8b8; margin-top:8px; font-style:italic;">
        Click a community on the map to select it in the dropdown.
    </div>"""

def choropleth_gradient_bar(colors, vmin, vmax, label):
    gradient_css = f"linear-gradient(to right, {', '.join(colors)})"
    return f"""
    <div style="margin-top:10px; padding-top:10px; border-top:1px solid #2a4a5a;">
        <div style="color:#F5A623; font-size:11px; text-transform:uppercase; letter-spacing:1px; margin-bottom:6px;">{label}</div>
        <div style="height:14px; border-radius:3px; background:{gradient_css}; margin-bottom:4px;"></div>
        <div style="display:flex; justify-content:space-between; font-size:10px; color:#8fa8b8;">
            <span>{vmin:.1f}%</span>
            <span>{vmax:.1f}%</span>
        </div>
    </div>"""

@pn.depends(parish_selector.param.value, community_selector.param.value)
def reactive_legend(selected_parish, selected_community):
    top = static_legend_top()

    if selected_parish == 'Island-Wide':
        burden_min = parish_gdf['avg_burden_pct'].min()
        burden_max = parish_gdf['avg_burden_pct'].max()
        gradient = choropleth_gradient_bar(
            ['#1a3a4a', '#F5A623', '#FF3D00'], burden_min, burden_max,
            "Energy Burden % (shown on map)"
        )
    else:
        key = parish_name_to_key.get(selected_parish)
        subset = poverty_gdf[poverty_gdf['_parish_key'] == key]
        pov_min = subset['Per_Tot_Po_numeric'].min()
        pov_max = subset['Per_Tot_Po_numeric'].max()
        if pd.notna(pov_min) and pd.notna(pov_max) and pov_min != pov_max:
            gradient = choropleth_gradient_bar(
                ['#1a3a4a', '#F5A623', '#FF3D00'], pov_min, pov_max,
                f"Poverty Rate % — {selected_parish}"
            )
        else:
            gradient = """<div style="margin-top:10px; padding-top:10px; border-top:1px solid #2a4a5a;
                font-size:10px; color:#8fa8b8; font-style:italic;">
                No poverty rate gradient available for this parish.</div>"""

    full_html = top + gradient + "\n</div>"
    return pn.pane.HTML(full_html, width=280)

# --- 9. Reactive Energy Burden Card ---
@pn.depends(parish_selector.param.value)
def energy_burden_card(selected_parish):
    if selected_parish == 'Island-Wide':
        value = f"{ISLAND_AVG_BURDEN:.1f}%"
        label = "Energy Burden (Island Avg)"
    else:
        row = lookup_burden_row(selected_parish)
        if row is not None:
            value = f"{row['avg_burden_pct']:.1f}%"
            label = f"Energy Burden ({selected_parish})"
        else:
            value = "N/A"
            label = "Energy Burden"
    return metric_card(label, value, color="#FF8C00")

# --- 10. Sidebar ---
sidebar_content = pn.Column(
    pn.pane.Markdown("## BSC Metrics", styles={"color": "#F5A623"}),
    energy_burden_card,
    metric_card("Overall Resiliency", "—"),
    metric_card("Energy Justice",     "—"),
    metric_card("Carbon Intensity",   "—"),
    pn.layout.Divider(),
    parish_selector,
    community_selector,
    parish_info,
    pn.layout.Divider(),
    reactive_legend,
    width=300,
)

# --- 10b. Right Panel: Definitions & Methodology ---
DEFINITIONS_CONTENT = {
    "What is Energy Burden?": """
**Energy Burden** is the share of a household's total spending that goes
toward electricity.

**Formula:**
`Energy Burden = (Annual Electricity Bill ÷ Total Annual Expenditure) × 100`

This is calculated per household, then averaged by parish. A burden above
5% is generally considered high for Jamaica — worth watching as a
benchmark when comparing parishes on this map.

Jamaica does not collect direct income data in its national household
survey (income is often underreported), so **total expenditure** is used
as the welfare measure instead — this is the standard, internationally
accepted substitute for income in this kind of analysis.
""",
    "What is Poverty Rate?": """
**Poverty Rate** (`Per_Tot_Po`) shown at the community level is the
estimated percentage of a community's population living below Jamaica's
poverty line.

This figure comes from a **2012 small-area statistical model**, not a
direct survey of every community. Jamaica's national household survey
(JSLC) only samples a few thousand households nationally — nowhere near
enough to report a reliable number for each of the island's ~1,600
communities individually.

To fill that gap, PIOJ/STATIN combined the sparse JSLC survey data with
the complete 2011 Population Census to statistically *estimate* poverty
at the community level. That means these numbers are modeled best
estimates, not direct measurements — genuinely useful for spotting
patterns, but with more uncertainty than parish-level survey figures.
""",
    "Data Sources & Methodology": """
**Energy Burden (parish-level):** Jamaica Survey of Living Conditions
(JSLC) 2023 Annual microdata, provided directly by PIOJ. Real household
survey responses — Annual Electricity Bill and Total Annual Expenditure
fields, calculated per household and averaged by parish.

**Poverty Rate (community-level):** 2012 small-area poverty model, PIOJ/
STATIN, combining JSLC survey data with the 2011 Population Census.

**Household Consumption (community-level):** Companion 2012 small-area
model estimating average annual household electricity expenditure.

**Population & Dwellings (community-level):** STATIN community-level
population and dwelling counts.

**Grid Infrastructure:** OpenStreetMap, extracted via Overpass Turbo —
transmission lines, substations, and power plants as mapped by OSM
contributors. Coverage may be incomplete in rural parishes.

**Parish Boundaries:** Official Jamaica parish administrative boundaries.
""",
    "About Vision 2030": """
Jamaica's **Vision 2030 National Development Plan** sets out the country's
long-term goals for economic, social, and environmental development,
including targets for renewable energy generation and reducing dependence
on imported fossil fuels.

This tool is built to help track progress toward those energy-specific
goals at a sub-national level — showing where energy burden and poverty
are concentrated, so decarbonization and energy-access investments can be
targeted more precisely, parish by parish and community by community.

The 50% renewable energy target referenced in the sidebar reflects
Jamaica's stated ambition under this framework.
""",
}

definitions_selector = pn.widgets.Select(
    name='Select a Topic',
    options=list(DEFINITIONS_CONTENT.keys()),
    width=270
)

def _simple_markdown_to_html(text):
    import re
    paragraphs = [p.strip() for p in text.strip().split("\n\n") if p.strip()]
    html_parts = []
    for p in paragraphs:
        p = re.sub(r'\*\*(.+?)\*\*', r'<b style="color:#F5A623">\1</b>', p)
        p = re.sub(r'`(.+?)`', r'<code style="background:#1a3a4a; padding:2px 5px; border-radius:3px; color:#00E5FF;">\1</code>', p)
        p = p.replace("\n", " ")
        html_parts.append(f"<p style='margin:0 0 12px 0;'>{p}</p>")
    return "".join(html_parts)

def render_definition_box(topic):
    content = DEFINITIONS_CONTENT.get(topic, "")
    html_body = _simple_markdown_to_html(content)
    return pn.pane.HTML(f"""
        <div style="background:#0f2535; border-left:4px solid #F5A623; border-radius:6px;
                    padding:16px 18px; font-family:monospace; color:#8fa8b8;
                    font-size:13px; line-height:1.6; box-sizing:border-box;">
            {html_body}
        </div>
    """, sizing_mode='stretch_width')

definitions_display = pn.bind(render_definition_box, definitions_selector)

right_panel_content = pn.Column(
    pn.pane.Markdown("## About This Data", styles={"color": "#F5A623"}),
    definitions_selector,
    definitions_display,
    width=300,
)

# --- 11. Main Layout ---
map_column = pn.Column(
    pn.pane.Markdown("# Map Overview", styles={"color": "#F5A623"}),
    map_view(),
    bottom_bar,
    sizing_mode='stretch_width',
    margin=10
)

# SURGICAL CHANGE: Map container takes up full center view alone now
main_content = pn.Row(
    map_column,
    sizing_mode='stretch_both',
)

# --- 12. Template ---
# SURGICAL CHANGE: Fed right_panel_content into native right_sidebar slot
template = pn.template.FastListTemplate(
    title="Jamaica Energy Atlas",
    sidebar_width=320,
    right_sidebar_width=320,
    collapsed_right_sidebar=False,
    theme='dark',
    accent_base_color="#0f2535",
    header_background="#0f2535",
    sidebar=[sidebar_content],
    right_sidebar=[right_panel_content],
    main=[main_content],
)

template.servable()
