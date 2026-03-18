import pdfplumber, sys
pdf_path = 'output/06049_保利物业/06049_2024_年报.pdf'
keywords = [
    '受限制', '已抵押', '已質押', '銀行存款',
    '賬齡', '帳齡', '貿易應收',
    '關連', '持續關連交易',
    '或然負債', '或有負債', '承擔', '資本承擔',
    '非經常性', '其他收入及收益', '其他收益及虧損',
    '附屬公司', '於附屬公司的權益', '子公司',
    '公司管治報告', '企業管治報告',
    '董事會報告', '管理層討論', '業務回顧',
    '綜合財務報表附註', '財務報表附註',
    '重要會計政策',
    '受限制銀行存款', '已抵押銀行存款',
    '經營租賃', '資本開支承擔',
    '主要附屬公司', '附屬公司名單',
    'Contingent', 'Pledged', 'Restricted',
    'Trade receivables', 'Aging',
    'Connected transaction', 'Subsidiaries',
    'Capital commitments', 'Operating lease',
]
with pdfplumber.open(pdf_path) as pdf:
    results = {}
    for kw in keywords:
        results[kw] = []
    for i, page in enumerate(pdf.pages):
        text = page.extract_text() or ''
        for kw in keywords:
            if kw.lower() in text.lower():
                pos = text.lower().find(kw.lower())
                context = text[max(0, pos-50):min(len(text), pos+80)].replace('\n', ' ')
                results[kw].append((i+1, context.strip()))
    for kw, hits in sorted(results.items()):
        if hits:
            pages = [h[0] for h in hits]
            print(f'=== {kw} === (pages: {pages[:10]})')
            for p, ctx in hits[:3]:
                print(f'  p.{p}: ...{ctx[:130]}...')
