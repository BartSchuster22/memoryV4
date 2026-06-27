CREATE TABLE roles (
    name TEXT PRIMARY KEY,
    description TEXT NOT NULL
);
INSERT INTO roles(name, description) VALUES
  ('canonical','verified durable fact'),
  ('active','currently useful working memory'),
  ('evidence','supporting evidence'),
  ('exhaust','low-value retained exhaust');
CREATE TABLE lifecycles (
    name TEXT PRIMARY KEY,
    terminal INTEGER NOT NULL CHECK (terminal IN (0,1))
);
INSERT INTO lifecycles(name, terminal) VALUES
  ('working',0),('live',0),('superseded',0),('archived',1),('expired',1);
