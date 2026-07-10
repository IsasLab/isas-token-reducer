# Verbose-phrase compression map

Maintained source for `reduce.py`'s phrase compression (`compress_phrases`).
Each line is `verbose phrase => concise equivalent`, lowercase. Only strictly
meaning-preserving substitutions belong here — no change to negation, quantity,
tense, modality, or emphasis. Entries are applied case-insensitively (the first
letter's case is preserved) and skip code and blockquotes.

Every entry below survived double adversarial review (two independent reviewers
had to agree it never changes meaning). See `references/techniques.md` for the
technique write-up.

<!-- SUBS-LIST-START -->
in order to => to
due to the fact that => because
in the event that => if
in the event of => if
at this point in time => now
at the present time => now
a large number of => many
a great deal of => much
has the ability to => can
have the ability to => can
in spite of the fact that => although
despite the fact that => although
with regard to => about
with respect to => about
in reference to => about
in the near future => soon
for the reason that => because
in light of the fact that => because
on account of the fact that => because
in the majority of cases => usually
the vast majority of => most
a sufficient number of => enough
in close proximity to => near
in the absence of => without
prior to => before
subsequent to => after
in addition to => besides
in conjunction with => with
for the purpose of => for
with the exception of => except
in order for => so
take into consideration => consider
make a decision => decide
give consideration to => consider
is able to => can
are able to => can
was able to => could
a number of => several
at all times => always
on a regular basis => regularly
in a timely manner => promptly
in the process of => currently
<!-- SUBS-LIST-END -->
