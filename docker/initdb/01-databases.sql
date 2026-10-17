-- Runs once, when the volume is first created. `assistant` is POSTGRES_DB.
CREATE DATABASE assistant_test;
CREATE DATABASE rezerve;
-- The evals: Rezerve's eval business on a database of its own, and the
-- agent's eval conversations apart from development's.
CREATE DATABASE rezerve_eval;
CREATE DATABASE assistant_eval;
