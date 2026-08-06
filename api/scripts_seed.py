"""Starting set of Arabic collection scripts, seeded once on first init.

Written for B2B debt collection with Saudi medical/aesthetic clinics — firm on
the money, respectful of the relationship, because these are customers who
will keep ordering after the balance is settled. Every script pushes toward a
specific, checkable commitment (a date, an amount, a name) rather than
accepting a vague promise, since a vague promise is not something you can
follow up on.

Never overwritten on reseed: db.init() only inserts these if the scripts
table is empty, so anything the collector edits or adds themselves survives a
redeploy. is_custom=0 marks these as the starting set, distinguishing them
from anything added later at is_custom=1.
"""

SCRIPTS = [
    # ---- delay / vague promise -------------------------------------------------
    {
        'category': 'Asks to delay',
        'trigger_text': 'Says "next week" / "soon" with no specifics',
        'script_ar': 'تمام، بس عشان أقدر أتابع معاك صح، ممكن نحدد يوم بالتاريخ ومبلغ معين؟ '
                     'مثلاً يوم الأحد الجاي كام تقدروا تحولوا؟',
        'tip': 'Never end the call with "next week" alone. Always leave with a specific '
               'date and a specific amount — that becomes the next follow-up, not a guess.',
    },
    {
        'category': 'Asks to delay',
        'trigger_text': 'Says "بكرة إن شاء الله" (tomorrow, God willing)',
        'script_ar': 'إن شاء الله خير. عشان أأكد المتابعة، هل تقدر ترسل لي صورة من إشعار '
                     'التحويل بعد ما يتم، أو أتصل بيك الساعة كام بكرة أتأكد؟',
        'tip': 'Pin down a callback time, and ask for proof of transfer as soon as it '
               'happens rather than waiting to find out.',
    },
    # ---- decision-maker unavailable --------------------------------------------
    {
        'category': 'Decision-maker unavailable',
        'trigger_text': '"المدير مش موجود دلوقتي" (the manager isn\'t here)',
        'script_ar': 'ولا يهمك، ممكن أعرف اسمك وصفتك في الشركة عشان أسجلها عندي؟ '
                     'وأقدر أتواصل معاه على رقم مباشر، ولا أفضل أرجع أتصل امتى؟',
        'tip': 'Always get the name and position of whoever you actually spoke to — log '
               'it in Contacts. If this keeps happening with the same excuse, that itself '
               'is a signal worth escalating.',
    },
    {
        'category': 'Decision-maker unavailable',
        'trigger_text': 'Gatekeeper won\'t connect you or take a message',
        'script_ar': 'تمام، ممكن بس أسيب رسالة إن شركة درما سيتي تحاول التواصل بخصوص '
                     'رصيد مستحق، وإنه مهم إننا نتكلم قريب؟ ومتى أفضل وقت أتصل فيه؟',
        'tip': 'Keep it factual and calm — a gatekeeper repeating a firm, polite message '
               'verbatim to the decision-maker is more effective than sounding annoyed.',
    },
    # ---- claims already paid ---------------------------------------------------
    {
        'category': 'Claims already paid',
        'trigger_text': '"احنا دفعنا الفلوس دي" (we already paid this)',
        'script_ar': 'ممكن ترسل لي صورة من إشعار التحويل أو رقم المرجع بتاعه؟ '
                     'عشان أراجعه مع الحسابات فورًا وأرد عليك بالتأكيد بنفس اليوم.',
        'tip': 'Never argue the point on the phone — always ask for the proof, then verify.'
               ' If it checks out, thank them and update the record. If it does not, come '
               'back with the exact statement showing what is actually outstanding.',
    },
    {
        'category': 'Claims already paid',
        'trigger_text': 'Insists payment was sent but has no proof',
        'script_ar': 'مفهوم، بس معايا هنا إن آخر دفعة مسجلة كانت بتاريخ [التاريخ] بمبلغ '
                     '[المبلغ]. لو فيه تحويل بعدها ميظهرش عندنا، ممكن نراجعه سوا بالكشف؟',
        'tip': 'Quote the last recorded payment specifically — it signals you have real '
               'records, not just a generic reminder, and moves the conversation from '
               '"who\'s right" to "let\'s reconcile."',
    },
    # ---- disputes the invoice/amount -------------------------------------------
    {
        'category': 'Disputes the amount',
        'trigger_text': '"الفاتورة فيها غلط" / amount looks wrong to them',
        'script_ar': 'تمام، خلينا نراجعها مع بعض. أقدر أبعت لك كشف حساب كامل فيه كل '
                     'الفواتير والمبالغ المستحقة، وتقولي لي بالظبط الفاتورة اللي فيها خلاف '
                     'عشانها نحلها بسرعة.',
        'tip': 'A specific dispute is progress, not a stall — isolate it, resolve that one '
               'line, and the rest of the balance usually stops being "in dispute" too.',
    },
    {
        'category': 'Disputes the amount',
        'trigger_text': 'General "the numbers don\'t match ours" without detail',
        'script_ar': 'من غير مشكلة، ممكن ترسل لنا كشف الحساب اللي عندكم عشان نقارنه '
                     'بالي عندنا؟ أي فرق هنلاقيه هنوضحه فورًا.',
        'tip': 'Getting their version of the statement usually surfaces the real gap fast '
               '— often a missing credit note or an invoice sent to the wrong contact.',
    },
    # ---- avoids calls / goes quiet ---------------------------------------------
    {
        'category': 'Avoids calls / goes quiet',
        'trigger_text': 'Stopped answering after multiple attempts',
        'script_ar': 'أنا حاولت أتواصل معاك أكتر من مرة بخصوص الرصيد المستحق ومحدش رد. '
                     'حابب أفهم لو فيه مشكلة معينة تمنعكم من السداد عشان نقدر نساعد، '
                     'أو لازم نرتب زيارة شخصية نتكلم فيها وجهًا لوجه.',
        'tip': 'Name the pattern plainly and offer a face-to-face visit as the next step — '
               'it signals this will not just fade away, without being a threat.',
    },
    {
        'category': 'Avoids calls / goes quiet',
        'trigger_text': 'WhatsApp messages are seen but not answered',
        'script_ar': 'وصلتكم رسالتي بخصوص الرصيد المستحق البالغ [المبلغ]. محتاج رد منكم '
                     'اليوم بخصوص موعد السداد، وإلا هضطر أرفع الموضوع للإدارة عندنا لاتخاذ '
                     'الإجراء المناسب.',
        'tip': 'Reserve this firmer message for genuine silence after real attempts — using '
               'it too early burns the tone you need for the accounts that are just slow, '
               'not avoiding you.',
    },
    # ---- asks for installments / discount --------------------------------------
    {
        'category': 'Asks for installments or a discount',
        'trigger_text': 'Requests to split the balance into installments',
        'script_ar': 'ممكن نرتب تقسيط، بس محتاجين نحدد جدول واضح: كام قسط، وكل قسط '
                     'بكام وامتى، ويكون موثق ومتفق عليه من الطرفين. تحب نبدأ بأول دفعة '
                     'إمتى؟',
        'tip': 'Only agree to a schedule you can write down and log as a promise per '
               'installment — a vague "we\'ll pay in parts" is not a plan.',
    },
    {
        'category': 'Asks for installments or a discount',
        'trigger_text': 'Asks for a discount or write-off on part of the balance',
        'script_ar': 'أقدر أرفع الطلب للإدارة، بس محتاج أعرف الأول: هل الاعتراض على '
                     'مبلغ معين وليه، ولا هو طلب تخفيض بشكل عام؟ ده هيساعدني أوصل '
                     'الطلب صح.',
        'tip': 'Discount requests are a management decision, not yours to grant on the '
               'spot — take it back with specifics rather than agreeing or refusing on '
               'the phone.',
    },
    # ---- broken promise ---------------------------------------------------------
    {
        'category': 'Following up on a broken promise',
        'trigger_text': 'The date they promised has already passed',
        'script_ar': 'كنا متفقين إن السداد يكون يوم [التاريخ] بمبلغ [المبلغ]، ولسه ملحظناش '
                     'حاجة. ممكن تحدثني إيه اللي حصل، ونتفق على موعد جديد يكون أكيد '
                     'المرة دي؟',
        'tip': 'Reference the specific promise back to them — date and amount — rather '
               'than a generic reminder. It shows you track commitments, which raises '
               'the cost of breaking the next one.',
    },
    {
        'category': 'Following up on a broken promise',
        'trigger_text': 'Second broken promise from the same contact',
        'script_ar': 'ده هو الوعد التاني اللي بيتأجل من نفس الميعاد. عشان نقدر نمشي '
                     'قدام، محتاج نتكلم مع حد تاني في الإدارة معاكم، أو نحدد موعد زيارة '
                     'شخصية الأسبوع ده. مين أفضل حد أتكلم معاه؟',
        'tip': 'A pattern of broken promises with one contact is a cue to escalate to a '
               'different person or an in-person visit, not to keep repeating the same call.',
    },
    # ---- first visit / introduction ---------------------------------------------
    {
        'category': 'Opening a first visit',
        'trigger_text': 'Introducing yourself at a customer for the first time',
        'script_ar': 'السلام عليكم، معايا [الاسم] من شركة درما سيتي. أنا مسؤول متابعة '
                     'الحسابات، وجيت اليوم عشان نراجع سوا الرصيد المستحق ونشوف أحسن '
                     'طريقة نرتب بيها السداد.',
        'tip': 'Lead with the relationship ("we want to keep working together smoothly"), '
               'not just the number — it sets a cooperative tone for everything after.',
    },
    {
        'category': 'Opening a first visit',
        'trigger_text': 'Customer seems surprised or defensive that you showed up',
        'script_ar': 'الزيارة مش عشان فيه مشكلة، هي متابعة عادية للحسابات المستحقة زي '
                     'باقي عملائنا. حابين نتأكد إن كل حاجة واضحة ومفهومة من الطرفين.',
        'tip': 'Normalise the visit — framing it as routine rather than a special measure '
               'keeps the tone collaborative instead of confrontational.',
    },
    # ---- requesting the signed reconciliation -----------------------------------
    {
        'category': 'Requesting a signed reconciliation',
        'trigger_text': 'Asking the customer to sign off on the account statement',
        'script_ar': 'عشان نقفل أي خلاف على الرصيد، حابين تراجع كشف الحساب ده وتوقع '
                     'عليه بالموافقة على المبلغ المستحق وهو [المبلغ]. ده بيسهل علينا '
                     'الاتنين لو احتجنا نرجع للأرقام بعدين.',
        'tip': 'Present it as protecting both sides, not just collecting evidence — makes '
               'the customer more willing to sign rather than feeling cornered.',
    },
    {
        'category': 'Requesting a signed reconciliation',
        'trigger_text': 'Customer hesitates to sign',
        'script_ar': 'مفهوم، لو فيه أي بند مش متأكد منه ممكن نراجعه دلوقتي قبل ما توقع. '
                     'التوقيع مش معناه دفع فوري، هو بس تأكيد إن الرقم صحيح ومتفق عليه.',
        'tip': 'Separate "confirming the number" from "committing to pay right now" — '
               'hesitation is often about the second thing, not the first.',
    },
    # ---- complaint used as excuse ------------------------------------------------
    {
        'category': 'Complains about service as a reason not to pay',
        'trigger_text': 'Brings up an unrelated service complaint',
        'script_ar': 'آسف إن حصل ده، وهسجل الشكوى وأتابعها مع القسم المختص فورًا. بس '
                     'عشان الرصيد المستحق موضوع منفصل، ممكن نتفق عليه برضه ونحل '
                     'الشكوى بالتوازي؟',
        'tip': 'Acknowledge the complaint genuinely and actually log it — but keep the two '
               'threads separate so a real service issue doesn\'t become an indefinite '
               'excuse for an unrelated balance.',
    },
    # ---- confirming before ending the call ---------------------------------------
    {
        'category': 'Closing a call or visit',
        'trigger_text': 'Wrapping up after getting a commitment',
        'script_ar': 'تمام، يبقى متفقين: هتحولوا [المبلغ] يوم [التاريخ]. هبعتلك تذكير قبلها '
                     'بيوم، وهتواصل معاك بعد الموعد أتأكد إن كل حاجة تمام. شكرًا لوقتك.',
        'tip': 'Always restate the exact commitment out loud before hanging up — it is '
               'the single biggest thing that turns a friendly chat into a real promise.',
    },
    {
        'category': 'Closing a call or visit',
        'trigger_text': 'No commitment was reached this time',
        'script_ar': 'تمام، هرجع أتواصل معاك يوم [التاريخ] نكمل الموضوع. لو احتجت أي '
                     'حاجة من ناحيتنا قبل كده، تقدر تتواصل معايا مباشرة.',
        'tip': 'Even with no promise, always leave with your own specific next-contact '
               'date — never end a call open-ended.',
    },
]


def seed_if_empty(conn):
    from datetime import datetime, timezone
    row = conn.execute('SELECT COUNT(*) AS n FROM scripts').fetchone()
    if row['n'] > 0:
        return
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    conn.executemany(
        'INSERT INTO scripts (category, trigger_text, script_ar, tip, sort_order,'
        ' is_custom, created_at) VALUES (:category,:trigger_text,:script_ar,:tip,'
        ':sort_order,0,:created_at)',
        [{**s, 'sort_order': i, 'created_at': now} for i, s in enumerate(SCRIPTS)],
    )
