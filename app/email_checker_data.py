"""Static lists for the built-in email checker (app/email_checker.py). No network: everything is embedded here."""

# Throwaway / temporary-inbox services. A person who gives one of these never reads it, and mail often bounces.
DISPOSABLE = frozenset("""
0-mail.com 0815.ru 10minutemail.com 10minutemail.net 10minutemail.org 10minemail.com 1secmail.com 1secmail.net 1secmail.org
20minutemail.com 20minutemail.it 33mail.com 3d-painting.com 5mail.cf 7tags.com 9ox.net a-bc.net abyssmail.com
adwaterandstir.com agedmail.com ajaxapp.net akapost.com amilegit.com amiri.net anonbox.net anonymbox.com antispam24.de
armyspy.com artman-conception.com auroraoutlook.com binkmail.com bio-muesli.net bobmail.info bofthew.com bootybay.de
boun.cr bouncr.com brefmail.com bsnow.net bspamfree.org bugmenot.com buymoreplays.com byom.de cachedot.net casualdx.com
cellurl.com chammy.info cheatmail.de chogmail.com clrmail.com cmail.club  cock.li consumerriot.com
cool.fr.nf correo.blogos.net cosmorph.com courriel.fr.nf courrieltemporaire.com crazymailing.com cubiclink.com
curryworld.de cust.in dacoolest.com dandikmail.com dayrep.com dcemail.com deadaddress.com deadspam.com despam.it
despammed.com devnullmail.com dfgh.net digitalsanctuary.com dingbone.com discard.email discardmail.com discardmail.de
disposable.com disposable-email.ml disposableaddress.com disposableemailaddresses.com disposeamail.com disposemail.com
dispostable.com dm.w3internet.co.uk dodgeit.com dodgit.com dontreg.com dontsendmespam.de drdrb.com dump-email.info
dumpandjunk.com dumpmail.de dumpyemail.com  e4ward.com easytrashmail.com einrot.com einrot.de email60.com
emaildienst.de emailfake.com emailigo.de emailinfive.com emailisvalid.com emailmiser.com emailondeck.com
emailsensei.com emailtemporanea.com emailtemporanea.net emailtemporar.ro emailtemporario.com.br emailthe.net
emailtmp.com emailto.de emailwarden.com emailx.at.hm emailxfer.com emeil.in emeil.ir emz.net enterto.com ephemail.net
etranquil.com etranquil.net evopo.com explodemail.com express.net.ua eyepaste.com fakeinbox.com fakeinformation.com
fakemail.fr fakemailgenerator.com fakemailz.com fammix.com fansworldwide.de fantasymail.de fastacura.com fastchevy.com
fastchrysler.com fastkawasaki.com fastmazda.com fastmitsubishi.com fastnissan.com fastsubaru.com fastsuzuki.com
fasttoyota.com fastyamaha.com fiifke.de filzmail.com fivemail.de fixmail.tk fizmail.com fleckens.hu flyspam.com
footard.com forgetmail.com fr33mail.info frapmail.com freundin.ru front14.org fux0ringduh.com garliclife.com
gelitik.in get1mail.com get2mail.fr getairmail.com getmails.eu getnada.com getonemail.com ghosttexter.de
girlsundertheinfluence.com gishpuppy.com gmial.net goemailgo.com  gotmail.net gotmail.org gowikibooks.com
gowikicampus.com gowikifilms.com gowikigames.com gowikimusic.com gowikinetwork.com gowikitravel.com gowikitv.com
great-host.in greensloth.com grr.la gsrv.co.uk guerillamail.biz guerillamail.com guerillamail.de guerillamail.net
guerillamail.org guerrillamail.biz guerrillamail.com guerrillamail.de guerrillamail.info guerrillamail.net
guerrillamail.org guerrillamailblock.com gustr.com h8s.org hacccc.com haltospam.com harakirimail.com hartbot.de
hat-geld.de herp.in hidemail.de hidzz.com hmamail.com hochsitze.com hopemail.biz  hulapla.de ieatspam.eu
ieatspam.info ihateyoualot.info iheartspam.org imails.info imgof.com imstations.com inbax.tk  inboxalias.com
inboxclean.com inboxclean.org inboxproxy.com incognitomail.com incognitomail.net incognitomail.org instant-mail.de
ipoo.org irish2me.com iwi.net jetable.com jetable.fr.nf jetable.net jetable.org jnxjn.com jourrapide.com junk1e.com
kasmail.com kaspop.com keepmymail.com killmail.com killmail.net kimsdisk.com kingsq.ga kiois.com kloap.com klzlk.com
koszmail.pl kulturbetrieb.info kurzepost.de lags.us landmail.co lastmail.co lawlita.com letthemeatspam.com lhsdv.com
lifebyfood.com link2mail.net litedrop.com lol.ovpn.to lookugly.com lopl.co.cc lortemail.dk lovemeleaveme.com lr78.com
luckymail.org m4ilweb.info maboard.com mail-filter.com mail-temporaire.fr  mail.mezimages.net mail.zp.ua
mail1a.de mail21.cc mail2rss.org mail333.com mail4trash.com mailbidon.com mailbiz.biz mailblocks.com mailbucket.org
mailcat.biz mailcatch.com mailde.de mailde.info maildrop.cc maildrop.cf maildrop.ga maildrop.gq maildrop.ml maildu.de
maileater.com maileimer.de mailexpire.com mailfa.tk mailforspam.com mailfreeonline.com mailguard.me mailhazard.com
mailhazard.us mailhz.me mailimate.com mailin8r.com mailinater.com mailinator.com mailinator.net mailinator.org
mailinator2.com mailincubator.com mailismagic.com mailjunk.cf mailjunk.ga mailjunk.gq mailjunk.ml mailjunk.tk
mailme.gq mailme.ir mailme.lv mailme24.com mailmetrash.com mailmoat.com mailms.com mailnator.com mailnesia.com
mailnull.com mailorg.org mailpick.biz mailproxsy.com mailquack.com mailrock.biz mailsac.com mailscrap.com mailseal.de
 mailsiphon.com mailslapping.com mailslite.com mailtemp.info mailtome.de mailtothis.com mailtrash.net
mailtv.net mailtv.tv mailzilla.com mailzilla.org makemetheking.com manybrain.com mbx.cc mciek.com mega.zik.dj
meinspamschutz.de meltmail.com messagebeamer.de mezimages.net migmail.net migumail.com mierdamail.com mintemail.com
moakt.cc moakt.co moakt.com moakt.ws moncourrier.fr.nf monemail.fr.nf monmail.fr.nf msa.minsmail.com mt2009.com
mt2014.com mt2015.com mx0.wwwnew.eu my10minutemail.com mycleaninbox.net myemailboxy.com mymail-in.net mymailoasis.com
mynetstore.de  mypartyclip.de myphantomemail.com mysamp.de myspaceinc.com myspaceinc.net myspaceinc.org
myspacepimpedup.com myspamless.com mytempemail.com mytempmail.com mytrashmail.com nabuma.com 
nepwk.com nervmich.net nervtmich.net netmails.com netmails.net netzidiot.de neverbox.com  nincsmail.hu
nmail.cf nnh.com no-spam.ws noblepioneer.com nobulk.com noclickemail.com nogmailspam.info nomail.pw nomail.xl.cx
nomail2me.com nomorespamemails.com nospam.ze.tc nospam4.us nospamfor.us nospamthanks.info notmailinator.com nowhere.org
nowmymail.com nurfuerspam.de  objectmail.com obobbo.com odaymail.com oneoffemail.com onewaymail.com
onlatedotcom.info  oopi.org opayq.com ordinaryamerican.net otherinbox.com ourklips.com outlawspam.com
ovpn.to owlpic.com pancakemail.com paplease.com pcusers.otherinbox.com pepbot.com pfui.ru pimpedupmyspace.com
pjjkp.com plexolan.de  politikerclub.de poofy.org pookmail.com  privatdemail.net
proxymail.eu prtnx.com putthisinyourspamdatabase.com putthisinyourspamdatabase.com  quickinbox.com
rcpt.at reallymymail.com realtyalerts.ca recode.me recursor.net reliable-mail.com rhyta.com rmqkr.net 
rppkn.com rtrtr.com s0ny.net  safersignup.de safetymail.info safetypost.de sandelf.de saynotospams.com
schafmail.de schrott-email.de secretemail.de secure-mail.biz selfdestructingmail.com sendspamhere.com
senseless-entertainment.com services391.com sharklasers.com shieldedmail.com shiftmail.com shitmail.me shitware.nl
shmeriously.com   sinnlos-mail.de skeefmail.com slapsfromlastnight.com slaskpost.se
slopsbox.com smashmail.de smellfear.com snakemail.com sneakemail.com sneakmail.de snkmail.com sofimail.com
solvemail.info sogetthis.com soodonims.com spam.la spam.su spam4.me spamail.de spamarrest.com spamavert.com
spambob.com spambob.net spambob.org spambog.com spambog.de spambog.ru spambox.info spambox.irishspringrealty.com
spambox.us spamcero.com spamcon.org spamcorptastic.com spamcowboy.com spamcowboy.net spamcowboy.org spamday.com
spamex.com spamfree.eu spamfree24.com spamfree24.de spamfree24.eu spamfree24.info spamfree24.net spamfree24.org
spamgoes.in spamgourmet.com spamgourmet.net spamgourmet.org spamherelots.com spamhereplease.com spamhole.com
spamify.com spaml.com spaml.de spammotel.com spamobox.com spamoff.de spamslicer.com spamspot.com spamstack.net
spamthis.co.uk spamthisplease.com spamtrail.com spamtroll.net speed.1s.fr spoofmail.de stuffmail.de super-auswahl.de
supergreatmail.com supermailer.jp superrito.com superstachel.de suremail.info sweetxxx.de tafmail.com tagyourself.com
talkinator.com tapchicuoihoi.com teewars.org teleworm.com teleworm.us temp-mail.com temp-mail.de temp-mail.io
temp-mail.org temp-mail.ru temp.emeraldwebmail.com tempail.com tempalias.com tempe-mail.com tempemail.biz tempemail.com
tempemail.co.za tempemail.net tempinbox.co.uk tempinbox.com tempmail.eu tempmail.it tempmail.net tempmail.us tempmail2.com
tempmaildemo.com tempmailer.com tempmailer.de tempmailaddress.com tempomail.fr temporarily.de temporarioemail.com.br
temporaryemail.net temporaryemail.us temporaryforwarding.com temporaryinbox.com temporarymailaddress.com tempsky.com
tempthe.net tempymail.com thanksnospam.info thankyou2010.com thecloudindex.com thisisnotmyrealemail.com
thismail.net thrma.com throwam.com throwawayemailaddress.com throwawaymail.com tilien.com tittbit.in tizi.com
tmail.ws tmailinator.com toiea.com toomail.biz topranklist.de tradermail.info trash-amil.com trash-mail.at
trash-mail.cf trash-mail.com trash-mail.de trash-mail.ga trash-mail.gq trash-mail.ml trash-mail.tk trash2009.com
trash2010.com trash2011.com trashdevil.com trashdevil.de trashemail.de trashmail.at trashmail.com trashmail.de
trashmail.me trashmail.net trashmail.org trashmail.ws trashmailer.com trashymail.com trashymail.net trbvm.com
trialmail.de trillianpro.com tryalert.com turual.com  twoweirdtricks.com tyldd.com uggsrock.com
 upliftnow.com uplipht.com uroid.com  valemail.net venompen.com veryrealemail.com viditag.com
viralplays.com vkcode.ru vomoto.com vpn.st vsimcard.com vubby.com wasteland.rfc822.org webemail.me webm4il.info
wegwerfadresse.de wegwerfemail.com wegwerfemail.de wegwerfmail.de wegwerfmail.info wegwerfmail.net wegwerfmail.org
wh4f.org whatiaas.com whatpaas.com whatsaas.com whopy.com whyspam.me wilemail.com willhackforfood.biz willselfdestruct.com
winemaven.info wronghead.com wuzup.net wuzupmail.net www.gishpuppy.com wwwnew.eu xagloo.com xemaps.com xents.com
xmaily.com xoxy.net xyzfree.net yapped.net  yep.it yogamaven.com yopmail.com yopmail.fr yopmail.net
 yuurok.com z1p.biz  zehnminuten.de zehnminutenmail.de zetmail.com zippymail.info zoaxe.com
zoemail.com zoemail.net zoemail.org zomg.info zxcv.com zxcvbnm.com 
""".split())

# Real, popular mailbox providers. Used to spot typos such as "gmial.com" (never stored on a lead as typed).
POPULAR = (
    "gmail.com", "yahoo.com", "yahoo.co.in", "yahoo.in", "yahoo.co.uk", "hotmail.com", "hotmail.co.uk", "outlook.com",
    "outlook.in", "live.com", "live.in", "rediffmail.com", "icloud.com", "me.com", "aol.com", "ymail.com", "msn.com",
    "protonmail.com", "proton.me", "zoho.com", "zohomail.in", "gmx.com", "mail.com", "rocketmail.com", "googlemail.com",
)

# Known typos mapped straight to the right domain (checked before the edit-distance rule).
KNOWN_TYPOS = {
    "gmial.com": "gmail.com", "gmai.com": "gmail.com", "gmail.co": "gmail.com", "gmail.con": "gmail.com", "gmail.cm": "gmail.com",
    "gmal.com": "gmail.com", "gmil.com": "gmail.com", "gmeil.com": "gmail.com", "gamil.com": "gmail.com", "gnail.com": "gmail.com",
    "gemail.com": "gmail.com", "gmail.om": "gmail.com", "gmail.comm": "gmail.com", "gmaill.com": "gmail.com", "ggmail.com": "gmail.com",
    "yahooo.com": "yahoo.com", "yaho.com": "yahoo.com", "yahou.com": "yahoo.com", "yhoo.com": "yahoo.com", "yahoo.con": "yahoo.com",
    "yahoo.co": "yahoo.com", "yaoo.com": "yahoo.com", "yahho.com": "yahoo.com", "tahoo.com": "yahoo.com",
    "yahoo.co.i": "yahoo.co.in", "yahoo.coin": "yahoo.co.in", "yahoo.com.in": "yahoo.co.in",
    "hotmial.com": "hotmail.com", "hotmal.com": "hotmail.com", "hotmai.com": "hotmail.com", "hotmil.com": "hotmail.com",
    "hotmail.con": "hotmail.com", "homail.com": "hotmail.com", "hotmaill.com": "hotmail.com",
    "outlok.com": "outlook.com", "outllok.com": "outlook.com", "outlook.con": "outlook.com", "outloook.com": "outlook.com",
    "outlool.com": "outlook.com", "otlook.com": "outlook.com",
    "rediffmal.com": "rediffmail.com", "redifmail.com": "rediffmail.com", "rediffmail.co": "rediffmail.com",
    "rediffmial.com": "rediffmail.com", "rediff.com.in": "rediffmail.com", "rediffmai.com": "rediffmail.com",
    "icloud.con": "icloud.com", "iclod.com": "icloud.com", "icoud.com": "icloud.com",
}

# Mailbox providers that give one person a personal address (not a company mailbox).
PERSONAL = frozenset(POPULAR) | {"rediffmail.in", "inbox.com", "fastmail.com", "tutanota.com", "yandex.com", "rediff.com"}
