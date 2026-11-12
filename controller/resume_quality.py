"""One content-preservation policy shared by drafting and measured refinements."""
from collections import Counter
import re

POLICY='''RESUME QUALITY POLICY (applies to both initial drafting and later refinements):
First tailor the CONTENT to the posting using evidence from ALL masters and confirmed profile facts. Identify the employer's main responsibilities and qualifications, then select, reorder, combine and rewrite the most relevant supported accomplishments. Reusing a master's layout is a starting point, not a requirement to keep its content or make only cosmetic edits. Preserve factual meaning and useful context, not every original sentence. In notes briefly explain the evidence selected, important keyword coverage and remaining unconfirmed requirements. After content selection, page-fit revisions should make only the wording changes needed for readable fit; they must not undo the tailoring or indiscriminately delete context.
Use the whole usable page within the existing margins, leaving normal breathing room (about 2-3 body lines at the bottom), when supported relevant content is available. A one-page PDF alone is not success. Keep or restore valuable source detail if the page is underfilled. Do not pad it with filler, generic soft skills, invented claims, enlarged fonts/spacing or unrelated material just to fill space.
For overflow during page-fit refinement, first shorten wrapped bullets by rewording: target a two-line bullet's spillover onto the second line so it fits one readable line, or a three-line bullet into two, when this preserves its meaning. Remove filler and redundant phrasing, not the accomplishment. Keep action, scope/method/tools, ownership and outcome/metrics together so the bullet still explains what the person did and why it mattered. Preserve natural, grammatical sentences; never force a bullet into one line by stripping useful context or turning it into cryptic fragments. Already concise bullets should stay unchanged at this fitting stage.
During page-fit refinement never reduce bullet count or remove existing sections, entries, names, roles, dates, qualifications, metrics or supported important keywords. Restoring an underfilled page may add back relevant source-supported bullets and entries. Do not delete whole bullets, jobs, projects, sections or their distinguishing context to meet a line target. A line target means recover that many RENDERED WRAPS by small wording edits, not delete that many source lines or bullets. Content selection for relevance belongs in initial tailoring and must be justified; it is not an emergency page-fit shortcut. If preserving meaning cannot meet the measured target, report the blocker instead of publishing a gutted resume.
Once a readable, well-filled one-page resume fits, stop. Keep fonts, margins and spacing; no typography compression, hidden/cropped content, page-count tricks or excessive blank space. Prefer an honest retained draft over a misleading success.
Keep ATS-friendly selectable text, conventional headings and logical reading order. Match the posting's important role-specific keywords to facts supported by source evidence or confirmed facts, using the employer's exact terminology naturally where accurate; never invent qualifications or stuff/repeat keywords. Documented actions may directly demonstrate a capability without naming that keyword. Never infer a new qualification solely from related experience; an unmentioned tool or proficiency needs its own evidence. Omit genuinely unsupported claims; optional factual follow-ups in notes must not delay drafting. Preserve actual titles, employers, dates, seniority and metrics; do not invent experience, qualifications or metrics.'''


def _body(text):
    # Source PDFs supply plain text rather than a TeX document.
    return text.split(r'\begin{document}',1)[-1].split(r'\end{document}',1)[0]


def _words(text):
    return len(re.findall(r"[^\W\d_][\w'-]*",re.sub(r'\\[A-Za-z@]+\*?',' ',_body(text))))


def bullets(text):
    return len(re.findall(r'\\(?:item|resumeItem|cvitem)\b',_body(text)))


def fuller_sources(inputs,tex):
    """An underfill review is useful only if sources contain omitted evidence.

    These counts trigger review, not automatic inclusion of facts or proof of
    relevance. Sparse source resumes must never be padded to hit a quota.
    """
    count=bullets(tex);words=_words(tex)
    return any(bullets(doc['text'])>count or _words(doc['text'])>words+max(15,words*.05)
               for doc in inputs.get('documents',[]))


def fill_target(metrics):
    if metrics['pages']!=1:return 0
    # Leave normal breathing room; never insist that ink touches the margin.
    allowed=max(3*metrics['line_pitch_pt'],.05*metrics['text_height_pt'])
    import math
    return max(0,math.ceil((metrics['first_page_spare_pt']-allowed-.25)/metrics['line_pitch_pt']))


def anchors(body):
    """Preserve recognized section/entry identities during wording-only fits."""
    body=body.translate({0x2010:'-',0x2011:'-',0x00a0:' '})
    names=r'ressection|section|cvsection|resumeSection|resumeSubheading|resumeProjectHeading|jobline|projline|eduline|cventry'
    result=Counter()
    for match in re.finditer(r'\\(?:'+names+r')\*?\b',body):
        pos=match.end()
        while True:
            start=pos
            while pos<len(body) and body[pos].isspace():pos+=1
            if pos>=len(body) or body[pos] not in '{[':pos=start;break
            opening=body[pos];closing='}' if opening=='{' else ']';depth=1;pos+=1
            while pos<len(body) and depth:
                if body[pos]=='\\':pos+=2;continue
                if body[pos]==opening:depth+=1
                if body[pos]==closing:depth-=1
                pos+=1
        result[re.sub(r'\s+',' ',body[match.start():pos]).strip()]+=1
    return result


def lost_content(before,after):
    if bullets(after)<bullets(before):return 'Revision deleted bullet points; reword wrapped bullets while preserving their count and meaning'
    if anchors(before)-anchors(after):return 'Revision removed or changed section/entry headings, links, roles or dates; preserve those identities'
    # Numbers are factual anchors, not a semantic verifier. Keep their literal
    # values while rewording the surrounding achievement.
    numeric=lambda text:Counter(re.findall(r'(?<![A-Za-z])\d+(?:[.,]\d+)*',text))
    if numeric(before)-numeric(after):return 'Revision removed numeric facts or metrics; preserve the achievements and dates while rewording'
    return ''
