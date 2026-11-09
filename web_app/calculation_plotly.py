"""Build the Plotly figure from the verified calculation snapshot."""


def rank_figure(rows):
    labels = [row['code'] for row in rows]
    return {
        'data': [
            {
                'type': 'bar',
                'orientation': 'h',
                'name': name,
                'x': [row[key]['rank'] for row in rows],
                'y': labels,
                'marker': {'color': color},
                'hovertemplate': '%{y} · ранг %{x}<extra>%{fullData.name}</extra>',
            }
            for key, name, color in (
                ('marcos', 'MARCOS', '#2457b6'),
                ('vikor', 'VIKOR', '#278779'),
            )
        ],
        'layout': {
            'barmode': 'group',
            'height': max(320, len(rows) * 44 + 105),
            'margin': {'l': 56, 'r': 18, 't': 16, 'b': 58},
            'legend': {'orientation': 'h', 'y': 1.08},
            'xaxis': {'title': {'text': 'Ранг (1 — первое место)'}, 'dtick': 1,
                      'range': [0, len(rows) + 0.5]},
            'yaxis': {'autorange': 'reversed', 'type': 'category'},
        },
    }
