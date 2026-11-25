"""Readable projection of durable evidence; never invent repair history."""
import json
from brain_service.remote_access import safe_text


def clean(value, limit=900):
    return safe_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), limit)


def cognitive_cards(incidents, memories):
    cards = []
    for row in incidents:
        evidence = row.get('evidence') or {}
        log = evidence.get('log') or {}
        hypotheses = row.get('hypotheses') or []
        component = row.get('component') or str(row.get('title', 'Incidente')).partition(': ')[2] or 'Servidor'
        title = ('Tempo limite · ' if str(row.get('title', '')).startswith('timeout:') else 'Ocorrência · ') + component
        cards.append({
            'id': 'incident:' + str(row['id']), 'title': clean(title, 150),
            'phase': 'investigating', 'label': 'EM INVESTIGAÇÃO', 'verified_repair': False,
            'problem': clean(log.get('message') or row.get('title', 'Sintoma registrado')),
            'strategy': clean(' '.join(str(item.get('test', '')) for item in hypotheses) or 'Correlacionar evidências e reproduzir a falha.'),
            'result': 'Correção ainda não comprovada. Esta estratégia é um próximo teste, não uma ação executada.',
            'sources': [clean(f"journal · {log.get('unit', row.get('component', ''))} · cursor {row.get('last_cursor')}")]
                       + [clean(item.get('line_ref', item.get('path', ''))) for item in evidence.get('sources', [])[:5]],
            'updated_at': row.get('last_seen'), 'occurrences': row.get('occurrences', 1),
        })
    for row in memories:
        fixed = row.get('status') in {'resolved', 'closed'} and row.get('outcome') in {'fixed', 'recovered'} and row.get('source_current') is True
        invalid = row.get('status') == 'invalidated' or row.get('source_current') is not True
        after = row.get('after') or {}
        cards.append({
            'id': 'memory:' + str(row['incident_id']), 'title': clean(row['incident_id'], 150),
            'phase': 'historical' if invalid else 'resolved' if fixed else 'verified',
            'label': 'EVIDÊNCIA ALTERADA' if invalid else 'CORREÇÃO REGISTRADA' if fixed else 'VERIFICAÇÃO REGISTRADA',
            'verified_repair': fixed, 'problem': clean(row.get('before', {})),
            'strategy': clean(after.get('strategy', 'Estratégia de correção não registrada nesta evidência.')),
            'result': clean(after), 'sources': [clean(item.get('line_ref', item.get('path', ''))) for item in row.get('evidence', [])[:6]],
            'updated_at': row.get('timestamp'), 'occurrences': 1,
        })
    # Timestamps can come from journal strings or epoch floats; normalize only for ordering.
    def priority(card):
        try:
            from datetime import datetime
            value = card['updated_at']
            timestamp = float(value) if not isinstance(value, str) else datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
        except (ValueError, TypeError, OverflowError):
            timestamp = 0
        return timestamp
    return sorted(cards, key=priority, reverse=True)[:100]
