# Data

`raw/bank-additional-full.csv` and `raw/bank-additional-names.txt` are the
**Bank Marketing** dataset from the UCI Machine Learning Repository, unchanged.

> Moro, S., Rita, P., & Cortez, P. (2014). *Bank Marketing* [Dataset].
> UCI Machine Learning Repository. https://doi.org/10.24432/C5K306
>
> S. Moro, P. Cortez and P. Rita. A Data-Driven Approach to Predict the Success of
> Bank Telemarketing. *Decision Support Systems*, 62:22-31, 2014.

Licence: CC BY 4.0. Source page: https://archive.ics.uci.edu/dataset/222/bank+marketing

41,188 rows, one per client contacted in a Portuguese bank's phone campaigns,
May 2008 – Nov 2010; target `y` = subscribed to a term deposit.

`processed/` is created by `src/etl.py` (star-schema tables for Power BI).
