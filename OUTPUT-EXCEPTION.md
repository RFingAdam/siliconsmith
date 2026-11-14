# Siliconsmith output exception

Additional permission under the GNU AGPL version 3, section 7.
Copyright (C) 2026 Adam Engelbrecht

Siliconsmith is licensed under the GNU Affero General Public License, version 3 or (at your
option) any later version. This additional permission applies to the whole of Siliconsmith's
AGPL-licensed code, whether or not a file carries a notice pointing to it. It exists so that your
chip design is never encumbered by Siliconsmith's license.

In this text, "your project" means a hardware design project, such as a Tiny Tapeout repository,
that you create or work on with Siliconsmith.

1. **Generated files.** You may use, copy, modify and distribute the files that Siliconsmith
   itself generates in your project when it runs (for example the files `create_project` makes,
   the Tiny Tapeout wrapper, emitted Verilog, the synthesis scripts under `build/` and
   `docs/verification.md`), including any text Siliconsmith copies into them from its templates
   or its own source, under the Apache License, Version 2.0, instead of the GNU AGPL. This does
   not cover content that you or your agent supply through tools such as `write_rtl`,
   `write_test` or `write_spec`; item 3 covers that content.
2. **Copied text.** The same applies to text that Siliconsmith returns at run time (tool
   results, messages and hints), and to the code examples in its skills and docs, when you, or an
   agent working for you, copy that text into your project.
3. **Your work.** Siliconsmith claims no rights in designs, tests, specifications or other
   material that you, or an agent working for you, write.
4. **Siliconsmith itself.** This permission never covers Siliconsmith's program: its Python
   package, scripts, Docker files, or any substantial part of them, however that code reaches a
   project. That code stays under the GNU AGPL, and using, modifying, distributing or offering it
   over a network is governed by the GNU AGPL alone.
5. **Removal.** Anyone who conveys a copy of Siliconsmith may remove this permission from that
   copy, as section 7 of the GNU AGPL allows. Output you already have keeps it.

The templates under `src/siliconsmith/templates/`, the skills under `src/siliconsmith/skills/`,
`docs/glossary.md` and the example designs under `examples/` are already licensed under
Apache-2.0 and do not need this permission.
