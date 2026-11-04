"""Bounded lexical retrieval: code identifiers, normalized words and Chinese fragments."""
from collections import Counter
import math
import re
import unicodedata

STRATEGY='bm25_code_words_han_v2'
STOP={'a','i','the','and','for','this','that','with','fix'}


def _tokens(value,han_unigrams=False):
    value=unicodedata.normalize('NFKC',value)
    result=[]
    for word in re.findall(r'[A-Za-z0-9]+',value):
        result.append(word.casefold())
        split=re.sub(r'([A-Z]+)([A-Z][a-z])',r'\1 \2',word)
        split=re.sub(r'([a-z0-9])([A-Z])',r'\1 \2',split).casefold().split()
        if split!=[word.casefold()]:result.extend(split)
    for run in re.findall(r'[\u3400-\u9fff]+',value):
        if len(run)>1:result.extend(run[i:i+2] for i in range(len(run)-1))
        if han_unigrams or len(run)==1:result.extend(run)
    return [t for t in result if t not in STOP and (len(t)>1 or not t.isdigit())][:4096]


def terms(value):
    return _tokens(value)


def matched_terms(query,document):
    single=bool(re.fullmatch(r'\s*[\u3400-\u9fff]\s*',unicodedata.normalize('NFKC',query)))
    return [t[:80] for t in sorted(set(_tokens(query,single)[:128]) & set(_tokens(document,single)))[:16]]


def rank(query, documents):
    """BM25 (k1=1.2,b=.75). No hidden network, embedding model or learned relevance claim."""
    single=bool(re.fullmatch(r'\s*[\u3400-\u9fff]\s*',unicodedata.normalize('NFKC',query)))
    query = sorted(set(_tokens(query,single)[:128]))
    counts = {identity: Counter(_tokens(body,single)) for identity, body in documents.items()}
    average = sum(sum(c.values()) for c in counts.values()) / max(1,len(counts)) or 1
    frequency = {t:sum(t in c for c in counts.values()) for t in query}
    result = []
    for identity, count in counts.items():
        score = 0.0
        for term in query:
            f = count[term]
            if f:
                idf = math.log(1 + (len(counts)-frequency[term]+.5)/(frequency[term]+.5))
                score += idf * f*2.2/(f+1.2*(.25+.75*sum(count.values())/average))
        if score:
            result.append((score,identity))
    return sorted(result,key=lambda row:(-row[0],row[1]))
