//! Private proof adapter. Raw parser errors, names and paths never reach stdout.
//! Unlike upstream's convenience converter, preserve and enforce lower reports.
use std::{collections::BTreeSet, fs, path::Path, process::ExitCode};
use serde_json::{Value, json};

fn read_proof(input: &Path, unit: &str, output: &Path) -> Value {
    let unit_m = match unit {
        "mm" => 0.001, "cm" => 0.01, "m" => 1.0,
        "in" => 0.0254, "ft" => 0.3048,
        _ => return json!({"status":"rejected", "code":"source_units_required"}),
    };
    let Ok(bytes) = fs::read(input) else {
        return json!({"status":"rejected", "code":"source_unavailable"});
    };
    if bytes.is_empty() || bytes.len() > 50 * 1024 * 1024 {
        return json!({"status":"rejected", "code":"source_size_invalid"});
    }
    let Ok(raw) = xt_parser::parse_raw_owned(xt_parser::decode(bytes)) else {
        return json!({"status":"rejected", "code":"source_read_failed"});
    };
    let faces: BTreeSet<usize> = raw.entities.iter()
        .filter(|e| e.type_id == xt_parser::schema::FACE).map(|e| e.index).collect();
    let bodies = raw.entities.iter()
        .filter(|e| e.type_id == xt_parser::schema::BODY).count();
    let mut mapped = BTreeSet::new();
    let mut skipped = 0;
    let lowered = cad_xt::topo::lower_bodies(&raw.entities, cad_xt::topo::DEFAULT_TOLERANCE);
    for body in &lowered {
        mapped.extend(body.face_sources.iter().copied());
        skipped += body.skipped.len();
    }
    let complete = raw.truncated.is_none() && skipped == 0 && !faces.is_empty()
        && bodies > 0 && mapped == faces && lowered.len() == bodies;
    let mut audit = json!({
        "schemaVersion":1, "status":"rejected", "code":"source_incomplete",
        "sourceRead":true, "truncated":raw.truncated.is_some(),
        "sourceBodies":bodies, "sourceFaces":faces.len(),
        "mappedFaces":mapped.len(), "skippedEntities":skipped,
        "sourceCoverage":complete,
        "sourceFaceIds":faces.into_iter().collect::<Vec<_>>(),
        "sourceUnit":unit, "outputUnit":"m",
        "unitScale":unit_m / 1000.0,
    });
    drop(lowered); drop(raw);
    if !complete { return audit; }

    let resolver = cad_ir::MaterialResolver {
        no_inference:true,
        library:cad_ir::sldmat::SldLibrary::default(),
        ..Default::default()
    };
    let Ok((mut scene, report)) = cad_xt::scene_from_file(
        input, &cad_xt::LowerOptions { materials:resolver }
    ) else { audit["code"] = json!("lower_failed"); return audit; };
    let scene_faces: usize = scene.geometry.iter()
        .filter_map(|g| g.brep.as_ref()).map(|b| b.faces.len()).sum();
    audit["lowerDiagnostics"] = json!(report.diagnostics.len());
    audit["sceneFaces"] = json!(scene_faces);
    if report.truncated.is_some() || !report.skipped.is_empty()
        || !report.diagnostics.is_empty() || scene.geometry.len() != bodies
        || scene_faces != audit["sourceFaces"].as_u64().unwrap_or(0) as usize {
        audit["code"] = json!("lower_incomplete"); return audit;
    }
    // cad-xt assumes source metres and expands them 1000x into its mm scene.
    // Honor Auto3D's explicit actual source unit instead of guessing it.
    let source_to_scene_mm = 1000.0 / (unit_m * 1000.0);
    let options = cad_tess::Options {
        linear_deflection:0.1 * source_to_scene_mm,
        relative:false, release_brep:false, ..Default::default()
    };
    let mesh = cad_tess::tessellate_scene(&mut scene, &options);
    audit["meshedFaces"] = json!(mesh.faces_ok);
    audit["failedFaces"] = json!(mesh.failed.len());
    audit["triangles"] = json!(mesh.triangles);
    let mesh_failure = if mesh.cancelled { "mesh_cancelled" }
        else if !mesh.failed.is_empty() { "mesh_face_failure" }
        else if mesh.faces_ok != scene_faces { "mesh_coverage_failed" }
        else if mesh.triangles == 0 { "mesh_empty" }
        else if mesh.triangles > 2_000_000 { "mesh_budget_exceeded" }
        else { "" };
    if !mesh_failure.is_empty() { audit["code"] = json!(mesh_failure); return audit; }
    scene.meta.source.clear(); scene.meta.authoring_tool.clear();
    for (i, g) in scene.geometry.iter_mut().enumerate() { g.name = format!("body-{}", i + 1); }
    for (i, n) in scene.nodes.iter_mut().enumerate() { n.name = format!("node-{}", i + 1); }
    for (i, m) in scene.materials.iter_mut().enumerate() { m.name = format!("source-color-{}", i + 1); }
    let export = cad_export::Options {
        unit_scale:unit_m / 1000.0,
        generator:"Auto3D CadConvert proof 0.1.0".into(),
        compression:cad_export::Compression::None,
        ..Default::default()
    };
    match cad_export::glb::write_file(&scene, &export, output) {
        Ok(n) if n > 0 && n <= 64 * 1024 * 1024 => {
            audit["status"] = json!("meshed"); audit["code"] = json!("independent_validation_required");
            audit["bytes"] = json!(n);
        }
        _ => { let _ = fs::remove_file(output); audit["code"] = json!("export_failed"); }
    }
    audit
}

fn main() -> ExitCode {
    std::panic::set_hook(Box::new(|_| {}));
    let args: Vec<String> = std::env::args().collect();
    if args.len() != 5 { eprintln!("Expected source, actual unit, output, audit."); return ExitCode::FAILURE; }
    let audit = std::panic::catch_unwind(|| read_proof(
        Path::new(&args[1]), &args[2], Path::new(&args[3])
    )).unwrap_or_else(|_| json!({"status":"rejected", "code":"reader_panicked"}));
    if fs::write(&args[4], serde_json::to_vec(&audit).unwrap()).is_err() { return ExitCode::FAILURE; }
    if audit["status"] == "meshed" { ExitCode::SUCCESS } else { ExitCode::FAILURE }
}
