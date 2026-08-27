# ISTRUZIONI PER COSTRUIRE UN BUNDLE SEMANTIC SEDNA VALIDO

Devi produrre un JSON che valida contro `SemanticDraftBundle` di Sedna. Leggi il
writeup, estrai la catena d'attacco (recon → enumeration → exploit → privesc →
flags) e costruisci il bundle. L'output DEVE essere il JSON puro del bundle.

## Struttura top-level
```json
{
  "artifacts": [ /* 1 DraftCase tipicamente */ ],
  "execution_examples": [ /* 0..N */ ],
  "ignored_segment_indexes": [ /* indici segmento non citati */ ]
}
```

## DraftCase (artifacts[0])
Campi obbligatori:
- `draft_type`: "case"
- `artifact_type`: "case"
- `knowledge_role`: "case_study" (o "negative_case")
- `local_id`: stringa sicura (solo A-Za-z0-9._-), unica nel bundle
- `origin`: "explicit" | "inferred" | "derived"
- `title`: stringa non vuota
- `starting_access`: stringa non vuota
- `source_quality`: "complete" | "partial" | "minimal" | "unusable"
- `difficulty`: opzionale
- `outcome`: stringa non vuota
- `transferable_properties`: lista stringhe (lezioni riusabili)
- `non_transferable_properties`: lista stringhe
- `steps`: lista di DraftCaseStep
- `citations`: lista con almeno 1 elemento `{"segment_indexes": [int,...]}`

## DraftCaseStep (in steps)
Campi obbligatori:
- `artifact_type`: "case_step"
- `local_id`: unico
- `ordinal`: int, DEVE partire da 1 e essere consecutivo (1,2,3,...)
- `state_before`: {"access": "...", "environment": [...], "privileges": [...]}
  -> `access` è OBBLIGATORIO (stringa non vuota) sia in before che after
- `state_after`: idem
- `observations`: lista stringhe
- `hypotheses`: lista di {"statement": "...", "origin": "..."}
- `selected_action`: {"intent": "...", "capability_ref": null o "..."}
- `evidence`: lista di {"summary": "...", "origin": "...", "category": "..."}
- `negative_evidence`: lista (opzionale)
- `transfer_conditions`: lista (opzionale)
- `case_specific_details`: lista (opzionale)
- `origin`: "explicit" | ...
- `citations`: almeno 1 elemento

## DraftExecutionExample (in execution_examples)
Campi obbligatori:
- `local_id`: unico
- `parent_local_id`: DEVE essere il local_id di una reference o di un case-step
  (NON il local_id del case!). Se non c'è un step/reference valido, NON includere
  execution_examples.
- `command_template`: stringa con placeholder `{{nome}}`
- `placeholders`: lista di:
  {"name": "nome", "kind": "target|port|username|credential_ref|source_case_credential|wordlist|path|value",
   "binding_policy": "authorized_scope|host_supplied|never_auto_bind", "role": "..."}
  REGOLA: kind=target -> binding_policy="authorized_scope";
          kind=source_case_credential -> binding_policy="never_auto_bind";
          tutti gli altri -> binding_policy="host_supplied"
  I token `{{name}}` nel command_template DEVONO corrispondere ESATTAMENTE ai name
  dichiarati (né più né meno).
- `capability_hint`: stringa
- `purpose`: stringa
- `observed_role`: stringa
- `prerequisites`: lista di {"statement": "...", "citations": [{"segment_indexes":[...]}]}
- `platform_constraints`: lista di {"dimension": "os_family|os_version|cpu_architecture|execution_environment",
  "relation": "required|compatible|incompatible", "value": "...", "citations": [{"segment_indexes":[...]}]}
- `citations`: almeno 1

## Segmenti
Ogni source ha N segmenti (0..N-1). Ogni segmento deve essere citato o elencato in
`ignored_segment_indexes`. Le citazioni `segment_indexes` usano gli indici GLOBALI.
Se un segmento non è citato da nessun artifact/step/example, aggiungilo a
`ignored_segment_indexes`.

## Regole chiave (errori comuni)
1. `state_before`/`state_after` DEVE avere `access` (non vuoto).
2. `parent_local_id` di un example punta a un STEP/REFERENCE, mai al case.
3. Ordinal consecutivi da 1.
4. Placeholder nel template == placeholder dichiarati (match esatto).
5. `origin` deve essere enum valido.
6. MAI includere flag reali o credenziali (redatta tutto).
7. Ogni `citations` ha almeno 1 elemento.

## Verifica
Devi validare l'output con:
```python
import sys; sys.path.insert(0, '/home/titagram/sedna/src')
from sedna.knowledge.semantic.drafts import SemanticDraftBundle
bundle = SemanticDraftBundle.model_validate(OUTPUT_DICT)
print("VALID:", len(bundle.artifacts))
```
Se fallisce, correggi finché non valida.

## Output richiesto
Salva il JSON (già valido) in `/tmp/bundles/<MACHINE>.json` e riporta solo:
- "VALID: YES, artifacts=N, steps=M, examples=K"
- oppure "VALID: NO" con l'errore
