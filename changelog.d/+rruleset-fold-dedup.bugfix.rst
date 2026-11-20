Fixed ``rruleset`` treating two occurrences at the same wall clock time as
duplicates of each other when a daylight-saving transition makes that time
happen twice. Occurrences on either side of the transition (differing in
``fold``) are distinct instants and are now all generated.
