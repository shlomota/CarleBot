import os
import mido
import numpy as np
from match_midi_agnostic import calculate_histogram, normalize_pitch_sequence
import logging
from multiprocessing import Pool, cpu_count
import chromadb
from functools import partial
from consts import MIDIS_DIR, CHROMA_CLIENT, MIDIS_COLLECTION
from tqdm import tqdm

# Constants
CHUNK_LENGTH = 20  # seconds
OVERLAP = 19.5  # seconds
MIN_NOTES = 20  # Minimum number of notes in a chunk

def midi_to_pitches_and_times(midi_file):
    midi = mido.MidiFile(midi_file)
    pitches = []
    times = []
    time = 0
    for msg in midi:
        time += msg.time
        if msg.type == 'note_on' and msg.velocity > 0:
            pitches.append(msg.note)
            times.append(time)
    return np.array(pitches), np.array(times)

def split_midi(pitches, times, chunk_length, overlap):
    chunks = []
    start_times = []
    num_chunks = int((times[-1] - chunk_length) // (chunk_length - overlap)) + 1
    for i in range(num_chunks):
        start_time = i * (chunk_length - overlap)
        end_time = start_time + chunk_length
        indices = np.where((times >= start_time) & (times < end_time))
        chunk_pitches = pitches[indices]
        chunks.append(chunk_pitches)
        start_times.append(start_time)
    return chunks, start_times

def process_midi_file(midi_path, track_name, chunk_length, overlap, min_notes):
    reference_pitches, reference_times = midi_to_pitches_and_times(midi_path)
    chunks, start_times = split_midi(reference_pitches, reference_times, chunk_length, overlap)

    filtered_chunks = []
    filtered_start_times = []
    filtered_track_names = []
    histograms = []

    for chunk, start_time in zip(chunks, start_times):
        if len(chunk) >= min_notes:
            filtered_chunks.append(chunk)
            filtered_start_times.append(start_time)
            filtered_track_names.append(track_name)
            normalized_chunk = normalize_pitch_sequence(chunk)
            histogram = calculate_histogram(normalized_chunk)
            histograms.append(histogram)

    return filtered_chunks, filtered_start_times, filtered_track_names, histograms

def add_midi_to_chromadb(midi_file_path, track_name):
    chunks, start_times, track_names, histograms = process_midi_file(midi_file_path, track_name, CHUNK_LENGTH, OVERLAP, MIN_NOTES)
    for chunk, start_time, histogram in zip(chunks, start_times, histograms):
        chunk_id = f"{track_name}_{start_time}"
        MIDIS_COLLECTION.add(
            documents=[str(chunk)],
            metadatas=[{
                "track_name": track_name,
                "start_time": start_time,
                "chunk_length": CHUNK_LENGTH,
                "note_sequence": ','.join(map(str, chunk.tolist())),  # Convert list to string
                "histogram_vector": ','.join(map(str, histogram.tolist()))  # Convert list to string
            }],
            ids=[chunk_id],
            embeddings=[histogram.tolist()]
        )



def load_chunks_to_chromadb(midi_dir):
    midi_files = []
    for root, _, files in os.walk(midi_dir):
        for file in files:
            if file.endswith('.mid'):
                midi_path = os.path.join(root, file)
                track_name = os.path.splitext(file)[0]
                midi_files.append((midi_path, track_name))

    process_midi_partial = partial(process_midi_file, chunk_length=CHUNK_LENGTH, overlap=OVERLAP, min_notes=MIN_NOTES)

    with Pool(processes=cpu_count()) as pool:
        results = pool.starmap(process_midi_partial, midi_files)

    documents = []
    metadatas = []
    ids = []
    embeddings = []

    for chunks, start_times, track_names_chunk, histograms in tqdm(results):
        for chunk, start_time, track_name, histogram in zip(chunks, start_times, track_names_chunk, histograms):
            chunk_id = f"{track_name}_{start_time}"
            documents.append(str(chunk))
            metadatas.append({
                "track_name": track_name,
                "start_time": start_time,
                "chunk_length": CHUNK_LENGTH,
                "note_sequence": ','.join(map(str, chunk.tolist())),  # Convert list to string
                "histogram_vector": ','.join(map(str, histogram.tolist()))  # Convert list to string
            })
            ids.append(chunk_id)
            embeddings.append(histogram.tolist())

            if len(documents) >= 10000:
                MIDIS_COLLECTION.add(
                    documents=documents,
                    metadatas=metadatas,
                    ids=ids,
                    embeddings=embeddings
                )
                documents = []
                metadatas = []
                ids = []
                embeddings = []

    # Insert any remaining documents
    if documents:
        MIDIS_COLLECTION.add(
            documents=documents,
            metadatas=metadatas,
            ids=ids,
            embeddings=embeddings
        )


if __name__ == "__main__":
    midi_dir = MIDIS_DIR
    load_chunks_to_chromadb(midi_dir)

