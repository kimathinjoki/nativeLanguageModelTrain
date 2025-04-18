import os
import docx
import pandas as pd
from transformers import MarianMTModel, MarianTokenizer
from transformers import DataCollatorForSeq2Seq, Seq2SeqTrainingArguments, Seq2SeqTrainer
import torch
from datasets import Dataset

def extract_text_from_docx(file_path):
    """Extract text from a DOCX file, separating by chapters and verses."""
    doc = docx.Document(file_path)
    
    chapters = []
    current_chapter = None
    current_verses = []
    
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        
        # Extract chapter headings (e.g., "Genesis 1", "Kîam 1")
        if para.style.name.startswith('Heading') and any(char.isdigit() for char in text):
            # Save previous chapter if exists
            if current_chapter and current_verses:
                chapters.append({
                    'title': current_chapter,
                    'verses': current_verses
                })
            
            current_chapter = text
            current_verses = []
        else:
            # Extract verses (verses typically have verse numbers)
            if any(char.isdigit() for char in text):
                current_verses.append(text)
    
    # Add the last chapter
    if current_chapter and current_verses:
        chapters.append({
            'title': current_chapter,
            'verses': current_verses
        })
    
    return chapters

def align_bible_texts(embu_path, english_path):
    """Align verses from Embu and English Bible texts."""
    print("Extracting text from Embu Bible...")
    embu_chapters = extract_text_from_docx(embu_path)
    
    print("Extracting text from English Bible...")
    english_chapters = extract_text_from_docx(english_path)
    
    # Align texts based on chapter order (assuming same order in both docs)
    # This is a simplistic alignment - may need manual verification
    aligned_texts = []
    
    min_chapters = min(len(embu_chapters), len(english_chapters))
    
    for i in range(min_chapters):
        embu_chapter = embu_chapters[i]
        english_chapter = english_chapters[i]
        
        min_verses = min(len(embu_chapter['verses']), len(english_chapter['verses']))
        
        for j in range(min_verses):
            embu_verse = embu_chapter['verses'][j]
            english_verse = english_chapter['verses'][j]
            
            # Extract just the verse text (remove verse numbers)
            embu_text = ' '.join(embu_verse.split()[1:])  # Skip the verse number
            english_text = ' '.join(english_verse.split()[1:])  # Skip the verse number
            
            if embu_text and english_text:
                aligned_texts.append({
                    'english': english_text,
                    'embu': embu_text
                })
    
    return pd.DataFrame(aligned_texts)

def prepare_dataset(df):
    """Prepare the dataset for training the model."""
    # Convert DataFrame to Dataset
    dataset = Dataset.from_pandas(df)
    
    # Split dataset into train and validation
    train_test_split = dataset.train_test_split(test_size=0.1)
    
    return {
        'train': train_test_split['train'],
        'validation': train_test_split['test']
    }

def preprocess_function(examples, tokenizer, max_length=128):
    """Preprocess the examples for training."""
    source_texts = examples["english"]
    target_texts = examples["embu"]
    
    # Tokenize inputs
    model_inputs = tokenizer(
        source_texts, 
        max_length=max_length, 
        padding="max_length", 
        truncation=True
    )
    
    # Tokenize targets
    with tokenizer.as_target_tokenizer():
        labels = tokenizer(
            target_texts, 
            max_length=max_length, 
            padding="max_length", 
            truncation=True
        )
    
    model_inputs["labels"] = labels["input_ids"]
    return model_inputs

def train_translation_model(dataset, output_dir, epochs=3):
    """Train a translation model from English to Embu."""
    # Initialize model with an existing translation model as base
    model_name = "Helsinki-NLP/opus-mt-en-mul"  # Multilingual model as base
    
    tokenizer = MarianTokenizer.from_pretrained(model_name)
    model = MarianMTModel.from_pretrained(model_name)
    
    # Preprocess the dataset
    tokenized_datasets = {}
    for split in dataset:
        tokenized_datasets[split] = dataset[split].map(
            lambda examples: preprocess_function(examples, tokenizer),
            batched=True
        )
    
    # Set up training arguments
    training_args = Seq2SeqTrainingArguments(
        output_dir=output_dir,
        evaluation_strategy="epoch",
        learning_rate=2e-5,
        per_device_train_batch_size=16,
        per_device_eval_batch_size=16,
        weight_decay=0.01,
        save_total_limit=3,
        num_train_epochs=epochs,
        predict_with_generate=True,
        fp16=torch.cuda.is_available(),  # Use FP16 if a GPU is available
        report_to="none"  # Don't report to wandb or tensorboard
    )
    
    # Initialize Trainer
    data_collator = DataCollatorForSeq2Seq(tokenizer, model=model)
    
    trainer = Seq2SeqTrainer(
        model=model,
        args=training_args,
        train_dataset=tokenized_datasets["train"],
        eval_dataset=tokenized_datasets["validation"],
        data_collator=data_collator,
        tokenizer=tokenizer,
    )
    
    # Train the model
    print("Starting training...")
    trainer.train()
    
    # Save the trained model
    model.save_pretrained(os.path.join(output_dir, "final_model"))
    tokenizer.save_pretrained(os.path.join(output_dir, "final_model"))
    
    return model, tokenizer

def test_model(model, tokenizer, test_sentences):
    """Test the trained model with some example sentences."""
    results = []
    
    for sentence in test_sentences:
        inputs = tokenizer(sentence, return_tensors="pt", padding=True)
        
        # Generate translation
        translated = model.generate(**inputs)
        translated_text = tokenizer.batch_decode(translated, skip_special_tokens=True)[0]
        
        results.append({
            "English": sentence,
            "Embu (translated)": translated_text
        })
    
    return pd.DataFrame(results)

def main():
    # File paths
    embu_bible_path = "../scrapper/bible_text/Complete_Bible_embu_full.docx"
    english_bible_path = "../scrapper/bible_text/English_NIV_Bible.docx"  # You'll need to create this
    
    # Check if English Bible exists, otherwise prompt user
    if not os.path.exists(english_bible_path):
        print(f"English Bible file not found at: {english_bible_path}")
        print("Please run the scrapper to download the English NIV Bible first.")
        return
    
    # Create directory for trained model
    output_dir = "./trained_models/en_embu_translator"
    os.makedirs(output_dir, exist_ok=True)
    
    # Align Bible texts
    print("Aligning Bible texts...")
    aligned_df = align_bible_texts(embu_bible_path, english_bible_path)
    
    # Save aligned texts
    aligned_df.to_csv(os.path.join(output_dir, "aligned_bible_texts.csv"), index=False)
    print(f"Saved {len(aligned_df)} aligned verse pairs")
    
    # Prepare dataset
    dataset = prepare_dataset(aligned_df)
    
    # Train model
    model, tokenizer = train_translation_model(dataset, output_dir)
    
    # Test with some example sentences
    test_sentences = [
        "God loves you.",
        "In the beginning God created the heavens and the earth.",
        "The Lord is my shepherd, I lack nothing.",
        "For God so loved the world that he gave his one and only Son."
    ]
    
    results = test_model(model, tokenizer, test_sentences)
    print("\nTest Results:")
    print(results)
    
    # Save test results
    results.to_csv(os.path.join(output_dir, "test_results.csv"), index=False)
    
    print("\nModel training complete. The model is saved in:", os.path.join(output_dir, "final_model"))

if __name__ == "__main__":
    main()