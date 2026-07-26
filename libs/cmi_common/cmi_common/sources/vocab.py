"""Static word lists backing the lexicon and the relevance gate.

Data only — no logic. Kept apart from ``lexicon.py`` so the resolution rules
stay readable.
"""

from __future__ import annotations

# Ordinary English words that are also crypto tickers OR single-word coin names.
# Intersecting this with the live universe yields the "ambiguous" set (tickers
# that must be corroborated) and marks prose coin names (which corroborate their
# own ticker but prove nothing alone). Uppercase.
#
# Curating this is the one manual duty the design keeps: a ticker missing here is
# believed on sight. The cost of a false entry is small (that coin then needs a
# cashtag or its full name, which crypto coverage almost always supplies); the
# cost of an omission is a bogus symbol feeding the decision engine. When in
# doubt, include the word.
COMMON_WORDS: frozenset[str] = frozenset("""
    APT ARB ATOM DOT ETC HYPE OP RUNE TON UNI VET
    AMP BEAM COMPOUND CURVE DASH FLARE GALA IMMUTABLE MAKER ORIGIN RENDER RIBBON
    STACKS STATUS THRESHOLD WAVES
    ATH PUMP APE IP ID ME AI GMT BAT CAKE TRUMP VIRTUAL USUAL GRASS SUPER PRIME
    LAYER DOGS PORTAL TURBO BANANA AUDIO TAO
    GRAPH SANDBOX INTERNET COMPUTER
    ONE TWO SIX TEN ALL AND ANY ARE ASK BAD BAG BAN BAND BANK BASE BEAR BEST BET
    BID BIG BIT BLUE BODY BOND BOOK BOOM BOOST BOT BOX BOY BULL BUY CALL CAN CAP
    CAR CARD CARE CASE CASH CAT CELL CHAT CITY CLUB COIN COLD COME CORE COST
    COVER CUT DATA DAY DEAL DEEP DOG DONE DOOR DOWN DRAW DROP DUE EACH EARN EAST
    EASY EDGE END EVEN EVER EYE FACE FACT FAIR FALL FAN FAR FAST FEE FEEL FEW
    FILE FILL FILM FIND FIRE FIRM FISH FIT FIVE FIX FLOW FLY FOOD FOOT FOR FORM
    FOUR FREE FROM FUEL FULL FUN FUND GAIN GAME GAS GATE GET GIFT GIVE GLOW GO
    GOAL GOLD GOOD GRID GROW HALF HAND HARD HAS HAT HAVE HEAD HEAR HEAT HELP
    HERE HIGH HILL HIT HOLD HOME HOPE HOT HOUR HOW HUB HUGE ICE ICON IDEA INCH
    INTO IRON ITEM JOB JOIN JUMP JUST KEEP KEY KID KIND KING KNOW LAB LAND LAST
    LATE LAW LAY LEAD LEAF LEAN LEFT LEG LESS LET LIFE LIFT LIGHT LIKE LINE LINK
    LIST LIVE LOAD LOAN LOCK LOG LONG LOOK LOSS LOT LOVE LOW LUCK MAIL MAIN MAKE
    MAN MANY MAP MARK MASK MASS MEAL MEAN MEET MEME MEN MESH MILE MILK MIND MINE
    MINT MISS MIX MODE MOON MORE MOST MOVE MUCH MUST NAME NEAR NECK NEED NET NEW
    NEWS NEXT NICE NINE NODE NONE NOON NORM NOSE NOT NOTE NOW NUT OFF OIL OLD ON
    ONCE ONLY OPEN OR ORDER OUR OUT OVER OWN PACE PACK PAGE PAID PAIN PAIR PAPER
    PARK PART PASS PAST PATH PAY PEAK PEN PEOPLE PET PICK PIE PIN PIPE PLAN PLAY
    PLUS POINT POOL POOR POP PORT POST POUR POWER PRESS PULL PUSH PUT RACE RAIN
    RANK RARE RATE RAW REACH READ REAL RED RENT REST RICH RIDE RING RISE RISK
    ROAD ROCK ROLE ROLL ROOM ROOT ROSE RULE RUN SAFE SAID SAIL SALE SALT SAME
    SAND SAVE SAY SEA SEAT SEE SEED SELF SELL SEND SET SHIP SHOP SHOT SHOW SIDE
    SIGN SILK SIT SITE SIZE SKIN SKY SLIP SLOW SNAP SNOW SOFT SOIL SOLD SOLE
    SOME SON SONG SOON SORT SOUL SOUP SPOT STAR STAY STEP STOP SUM SUN SURE SWAP
    TAG TAKE TALK TALL TAPE TASK TAX TEAM TECH TELL TEN TERM TEST TEXT THAN THAT
    THE THEM THEN THEY THIN THIS THUS TIDE TIE TILE TIME TINY TIP TOOL TOP TOUR
    TOWN TOY TRACK TRADE TRAIL TRAIN TREE TRIP TRUE TRUST TRY TURN TWIN TYPE
    UNIT UP US USE USER VAN VAST VERY VIEW VOTE WAIT WAKE WALK WALL WANT WAR
    WARM WASH WAVE WAY WEAK WEAR WEB WEEK WELL WENT WEST WET WHAT WHEN WHO WHY
    WIDE WIFE WILD WILL WIN WIND WINE WING WIRE WISE WISH WITH WOLF WOOD WORD
    WORK WORLD WORTH YARD YEAR YES YET YOU YOUR ZERO ZONE
    """.split())  # noqa: SIM905 -- readable columns beat a 300-item list literal

# Vocabulary that makes an item crypto-relevant even with no ticker in sight.
# Lowercase; matched on word boundaries against title + body.
CRYPTO_KEYWORDS: frozenset[str] = frozenset("""
    airdrop altcoin bitcoin blockchain bridge cbdc cex coinbase crypto
    cryptocurrency custody dao defi depeg derivatives dex etf exchange
    futures gas halving hodl kraken layer2 ledger leverage liquidation
    liquidity mempool mining nft onchain perpetual rollup rugpull sec
    seedphrase selfcustody smartcontract solidity stablecoin
    staking stakers tokenomics tvl validator wallet web3 whale zk zkproof
    binance tether ethereum
    """.split())  # noqa: SIM905 -- readable columns beat a 50-item list literal

# Cold-start fallback: an unreachable or empty Redis must degrade recall, not
# blank the lexicon and drop every item. (ticker, name) pairs.
SEED_COINS: tuple[tuple[str, str], ...] = (
    ("BTC", "Bitcoin"),
    ("ETH", "Ethereum"),
    ("USDT", "Tether"),
    ("BNB", "BNB"),
    ("SOL", "Solana"),
    ("USDC", "USD Coin"),
    ("XRP", "XRP"),
    ("DOGE", "Dogecoin"),
    ("ADA", "Cardano"),
    ("TRX", "TRON"),
    ("AVAX", "Avalanche"),
    ("SHIB", "Shiba Inu"),
    ("DOT", "Polkadot"),
    ("LINK", "Chainlink"),
    ("BCH", "Bitcoin Cash"),
    ("NEAR", "NEAR Protocol"),
    ("MATIC", "Polygon"),
    ("LTC", "Litecoin"),
    ("ICP", "Internet Computer"),
    ("UNI", "Uniswap"),
    ("APT", "Aptos"),
    ("XLM", "Stellar"),
    ("ETC", "Ethereum Classic"),
    ("ATOM", "Cosmos"),
    ("HBAR", "Hedera"),
    ("FIL", "Filecoin"),
    ("ARB", "Arbitrum"),
    ("VET", "VeChain"),
    ("OP", "Optimism"),
    ("MKR", "Maker"),
    ("INJ", "Injective"),
    ("SUI", "Sui"),
    ("GRT", "The Graph"),
    ("AAVE", "Aave"),
    ("RUNE", "THORChain"),
    ("ALGO", "Algorand"),
    ("SEI", "Sei"),
    ("TIA", "Celestia"),
    ("PEPE", "Pepe"),
    ("HYPE", "Hyperliquid"),
    ("TON", "Toncoin"),
    ("STX", "Stacks"),
    ("IMX", "Immutable"),
    ("RNDR", "Render"),
    ("FTM", "Fantom"),
    ("EGLD", "MultiversX"),
    ("SAND", "The Sandbox"),
    ("AXS", "Axie Infinity"),
    ("THETA", "Theta Network"),
    ("FLOW", "Flow"),
)
